"""명령줄 도구.

    novel-factory analyze novel.epub              참고작 분석 결과를 사람이 읽게 출력
    novel-factory analyze novel.txt --json        Reference Profile JSON
    novel-factory episodes novel.epub             회차 분리 결과만 확인
    novel-factory aggregate a.txt b.txt c.txt     여러 작품 집계 + 패턴 도출
    novel-factory plan-story hoegwi               사건 일정 + Arc 설계
    novel-factory write hoegwi 1 --to 30          1~30화 연속 생성 (LLM 필요)
    novel-factory check hoegwi 3                  3화 품질 검사 (LLM 없으면 규칙 검사만)
    novel-factory create --file 요청.txt          요청 글로 작품을 만들고 집필 예약
    novel-factory create "현대판타지 ... 250화" --write
                                                  만든 뒤 멈출 때까지 바로 쓴다
    novel-factory evaluate hoegwi --end 30        30화까지 설정 오류·패턴·복선 평가
    novel-factory run-schedule                    자동 집필이 켜진 작품을 지금 쓴다
    novel-factory run-schedule hoegwi             이 작품만 지금 쓴다 (꺼져 있어도)
    novel-factory serve                           API 서버 실행

write 말고는 LLM 없이 동작한다. check는 LLM이 있으면 Logic 검사까지 한다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from novel_factory.errors import NovelFactoryError
from novel_factory.reference import analyze_file
from novel_factory.reference.parser import SUPPORTED_EXTENSIONS, parse_file
from novel_factory.reference.pattern import aggregate_profiles, derive_patterns
from novel_factory.reference.structure import split_document


def _cmd_analyze(args: argparse.Namespace) -> int:
    result = analyze_file(
        args.path,
        reference_id=args.id or Path(args.path).stem,
        genre=args.genre,
        with_fingerprint=False,
    )
    if args.json:
        print(result.profile.to_json(include_names=args.show_names, verbose=args.verbose))
    else:
        print(result.profile.report())
        if args.show_names and result.profile.characters:
            print("\n■ 추출된 인물 (분석 확인용. 저장되지 않는다)")
            last = result.profile.characters.last_story_seq
            for c in result.profile.characters.characters:
                print(
                    f"  {c.name:8s} {c.role:6s} 등장 {c.occurrences:4d}회 "
                    f"{c.episode_coverage:3d}화 {c.first_episode}~{c.last_episode} "
                    f"{c.exit_style(last)}"
                )
    return 0


def _cmd_episodes(args: argparse.Namespace) -> int:
    document = parse_file(Path(args.path))
    result = split_document(document, fallback_chars=args.fallback_chars)
    episodes = result.story_episodes
    total = sum(e.char_count for e in episodes)
    print(
        f"분리 방식: {result.method}"
        + (f" (마커 '{result.marker_kind}')" if result.marker_kind else "")
    )
    average = total // max(len(episodes), 1)
    print(f"회차 {len(episodes)}편 / 총 {total:,}자 / 평균 {average:,}자\n")
    for e in episodes[: args.limit]:
        number = f"{e.number}" if e.number is not None else "-"
        print(
            f"  seq {e.seq:4d}  원본번호 {number:>4s}  {e.char_count:>7,}자  {e.title[:40]}"
        )
    if len(episodes) > args.limit:
        print(f"  … 외 {len(episodes) - args.limit}편")
    for warning in result.warnings:
        print(f"\n[경고] {warning}")
    return 0


def _cmd_aggregate(args: argparse.Namespace) -> int:
    profiles = []
    for path in args.paths:
        result = analyze_file(
            path, reference_id=Path(path).stem, genre=args.genre, with_fingerprint=False
        )
        profiles.append(result.profile)
        summary = result.profile.summary()
        print(
            f"  {Path(path).name:30s} {summary['episode_count']:4d}화 "
            f"대사 {summary['dialogue_ratio']:.0%} 훅 {summary['cliffhanger_rate']:.0%} "
            f"대형사건 {summary['major_event_interval']}화"
        )
    print()
    genre_profile = aggregate_profiles(profiles, genre=args.genre)
    print(genre_profile.describe())
    print("\n■ 도출된 패턴")
    for pattern in derive_patterns(genre_profile):
        print(f"  [{pattern.aspect}] {pattern.instruction}")
    return 0


def _open_novel(session, slug: str):
    from novel_factory.database.repositories import NovelRepository

    return NovelRepository(session).require_by_slug(slug)


def _cmd_plan_story(args: argparse.Namespace) -> int:
    from novel_factory.database import create_all, session_scope
    from novel_factory.generation.pipeline import plan_story
    from novel_factory.llm import get_provider

    create_all()
    provider = get_provider()
    with session_scope() as session:
        novel = _open_novel(session, args.slug)
        result = plan_story(
            session, novel, provider if provider.available else None, replace=args.replace
        )
        print(f"LLM 사용: {'예' if result.used_llm else '아니오 (골격만)'}")
        for arc in result.arcs:
            print(f"  {arc.order}. {arc.name}  {arc.start_episode}~{arc.end_episode}화")
        for warning in result.warnings:
            print(f"[경고] {warning}")
    return 0


def _cmd_write(args: argparse.Namespace) -> int:
    from novel_factory.database import create_all, session_scope
    from novel_factory.generation.pipeline import generate_episode
    from novel_factory.llm import get_provider

    create_all()
    provider = get_provider()
    if not provider.available:
        print(
            "LLM에 연결할 수 없습니다. NF_LLM_BASE_URL / NF_LLM_MODEL을 확인하세요.",
            file=sys.stderr,
        )
        return 3
    last = args.to or args.episode
    for number in range(args.episode, last + 1):
        # 회차마다 커밋한다. 중간에 멈춰도 앞 회차는 남는다.
        with session_scope() as session:
            novel = _open_novel(session, args.slug)
            result = generate_episode(
                session, novel, number, provider, replace=args.replace
            )
            info = result.as_dict()
            print(
                f"{number}화 '{info['title']}' {info['char_count']:,}자 "
                f"훅={info['hook_type'] or '없음'} → {info['folder']}"
            )
            quality = result.quality
            summary = str(quality.get("summary", "")).replace("\n", ", ")
            print(f"   품질: {quality.get('verdict')} ({summary})")
            for warning in result.warnings:
                print(f"   [경고] {warning}")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    """검사만 한다. 원고를 고치는 것은 write(생성 중 자동 수정)나 API의 fix=true."""
    from novel_factory.database import create_all, session_scope
    from novel_factory.database.repositories import EpisodeRepository
    from novel_factory.generation.pipeline import similarity_index_for
    from novel_factory.llm import get_provider
    from novel_factory.quality.runner import check_and_fix

    create_all()
    provider = get_provider()
    with session_scope() as session:
        novel = _open_novel(session, args.slug)
        episode = EpisodeRepository(session).get_by_number(novel.id, args.episode)
        if episode is None or not (episode.final_text or "").strip():
            print(f"{args.episode}화 원고가 없습니다.", file=sys.stderr)
            return 1
        outcome = check_and_fix(
            session,
            novel,
            episode,
            provider if provider.available else None,
            similarity_index=similarity_index_for(session, novel),
            fix=False,
            reader=args.reader or None,
        )
        print(outcome.report.summary())
        for issue in outcome.report.issues:
            scene = f" 장면{issue.scene_index + 1}" if issue.scene_index is not None else ""
            print(
                f"  [{issue.severity}] {issue.checker}/{issue.code}{scene}: {issue.message}"
            )
            if issue.quote:
                print(f'      "{issue.quote}"')
        for name, result in outcome.report.results.items():
            if result.skipped:
                print(f"  ({name} 건너뜀: {result.skipped})")
    return 0


def _cmd_create(args: argparse.Namespace) -> int:
    """기획안 57번: 요청 글 하나로 작품을 만든다."""
    from novel_factory.database import create_all, session_scope
    from novel_factory.llm import get_provider
    from novel_factory.orchestrator.project import ProjectOptions, start_project
    from novel_factory.orchestrator.request import parse_request
    from novel_factory.scheduler import jobs

    text = Path(args.file).read_text(encoding="utf-8") if args.file else args.text
    if not text.strip():
        print("요청 글을 주세요 (인자 또는 --file).", file=sys.stderr)
        return 2
    create_all()
    provider = get_provider()
    if not provider.available:
        print(
            "LLM에 연결할 수 없습니다. NF_LLM_BASE_URL / NF_LLM_MODEL을 확인하세요.",
            file=sys.stderr,
        )
        return 3
    request = parse_request(text, provider)
    print(
        f"장르 {request.genre} / {request.episodes}화 / "
        f"회차당 {request.chars_per_episode}자"
    )
    for ref in request.references:
        print(f"   참고 {ref.name}: {', '.join(ref.aspects) if ref.aspects else '전부'}")
    for w in request.warnings:
        print(f"   [경고] {w}")
    with session_scope() as session:
        result = start_project(
            session,
            request,
            provider,
            options=ProjectOptions(
                publishing_mode=args.publishing_mode,
                episodes_per_run=args.per_run,
                continuous=args.write,
            ),
            slug=args.slug,
        )
        novel_id = result.novel.id
        print(f"작품 '{result.novel.title}' ({result.novel.slug})")
        for step in result.steps:
            print(f"   {step['step']}: {step['status']}")
    if args.write:
        run = jobs.run_novel(novel_id, provider=provider, require_enabled=False)
        print(f"쓴 회차: {len(run.written)}개. {run.paused or run.error or ''}")
        return 1 if run.error else 0
    return 0


def _cmd_evaluate(args: argparse.Namespace) -> int:
    import json

    from novel_factory.database import create_all, session_scope
    from novel_factory.quality.evaluation import evaluate_novel

    create_all()
    with session_scope() as session:
        novel = _open_novel(session, args.slug)
        report = evaluate_novel(session, novel, start=args.start, end=args.end or None)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _cmd_run_schedule(args: argparse.Namespace) -> int:
    """서버 없이 스케줄러 작업을 한 번 돌린다. OS 작업 스케줄러에 걸어도 된다."""
    from novel_factory.database import create_all, session_scope
    from novel_factory.scheduler import jobs

    create_all()
    if args.slug:
        with session_scope() as session:
            novel_id = _open_novel(session, args.slug).id
        results = [jobs.run_novel(novel_id, require_enabled=False)]
    else:
        results = jobs.run_all()
        if not results:
            print("자동 집필이 켜진 작품이 없습니다 (PUT /novels/{slug}/schedule).")
    for r in results:
        written = ", ".join(f"{n}화" for n in r.written) or "없음"
        print(f"{r.slug}: 쓴 회차 {written}")
        for label, value in (
            ("건너뜀", r.skipped),
            ("멈춤", r.paused),
            ("오류", r.error),
        ):
            if value:
                print(f"   [{label}] {value}")
    return 1 if any(r.error for r in results) else 0


def _cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print("uvicorn이 없습니다: pip install 'uvicorn[standard]'", file=sys.stderr)
        return 1
    uvicorn.run(
        "novel_factory.app.main:app", host=args.host, port=args.port, reload=args.reload
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="novel-factory",
        description="참고소설을 구조로 분석하고 그 구조로 장편소설을 쓰는 시스템",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="참고작 한 편을 분석한다")
    analyze.add_argument(
        "path", help=f"지원 형식: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
    )
    analyze.add_argument("--id", default="", help="Reference ID (기본: 파일명)")
    analyze.add_argument("--genre", default="", help="장르")
    analyze.add_argument("--json", action="store_true", help="JSON으로 출력")
    analyze.add_argument("--verbose", action="store_true", help="복선 후보 등 상세 포함")
    analyze.add_argument(
        "--show-names",
        action="store_true",
        help="추출된 인물 이름을 화면에만 표시한다 (저장되지 않는다)",
    )
    analyze.set_defaults(func=_cmd_analyze)

    episodes = sub.add_parser("episodes", help="회차 분리 결과만 본다")
    episodes.add_argument("path")
    episodes.add_argument("--limit", type=int, default=20)
    episodes.add_argument("--fallback-chars", type=int, default=5000)
    episodes.set_defaults(func=_cmd_episodes)

    aggregate = sub.add_parser("aggregate", help="여러 작품을 집계해 패턴을 뽑는다")
    aggregate.add_argument("paths", nargs="+")
    aggregate.add_argument("--genre", default="")
    aggregate.set_defaults(func=_cmd_aggregate)

    plan = sub.add_parser(
        "plan-story", help="사건 일정과 Arc를 설계한다 (LLM 없으면 골격만)"
    )
    plan.add_argument("slug")
    plan.add_argument("--replace", action="store_true", help="기존 Arc를 갈아엎는다")
    plan.set_defaults(func=_cmd_plan_story)

    write = sub.add_parser(
        "write", help="회차를 계획부터 기억 갱신까지 생성한다 (LLM 필요)"
    )
    write.add_argument("slug")
    write.add_argument("episode", type=int)
    write.add_argument("--to", type=int, default=0, help="이 회차까지 연속 생성")
    write.add_argument("--replace", action="store_true", help="확정된 회차를 다시 만든다")
    write.set_defaults(func=_cmd_write)

    check = sub.add_parser("check", help="회차 품질 검사 (검사만, 원고는 고치지 않는다)")
    check.add_argument("slug")
    check.add_argument("episode", type=int)
    check.add_argument(
        "--reader", action="store_true", help="Reader Simulation도 돌린다 (LLM 필요)"
    )
    check.set_defaults(func=_cmd_check)

    create = sub.add_parser(
        "create", help="요청 글로 작품을 만든다 (기획안 57번, LLM 필요)"
    )
    create.add_argument("text", nargs="?", default="", help="요청 글")
    create.add_argument("--file", default="", help="요청 글 파일")
    create.add_argument("--slug", default="")
    create.add_argument(
        "--per-run", type=int, default=1, help="예약 실행 한 번에 쓸 회차 수"
    )
    create.add_argument(
        "--publishing-mode", choices=("manual", "automatic"), default="automatic"
    )
    create.add_argument(
        "--write",
        action="store_true",
        help="만든 뒤 멈출 때까지 바로 쓴다 (며칠 걸릴 수 있다)",
    )
    create.set_defaults(func=_cmd_create)

    evaluate = sub.add_parser("evaluate", help="장기 생성 평가 (기획안 55번)")
    evaluate.add_argument("slug")
    evaluate.add_argument("--start", type=int, default=1)
    evaluate.add_argument("--end", type=int, default=0)
    evaluate.set_defaults(func=_cmd_evaluate)

    run = sub.add_parser("run-schedule", help="자동 집필 작업을 지금 한 번 돌린다")
    run.add_argument(
        "slug", nargs="?", default="", help="이 작품만 (비우면 켜진 작품 전부)"
    )
    run.set_defaults(func=_cmd_run_schedule)

    serve = sub.add_parser("serve", help="API 서버를 띄운다")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=_cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except NovelFactoryError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
