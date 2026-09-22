"""명령줄 도구.

    novel-factory analyze novel.epub              참고작 분석 결과를 사람이 읽게 출력
    novel-factory analyze novel.txt --json        Reference Profile JSON
    novel-factory episodes novel.epub             회차 분리 결과만 확인
    novel-factory aggregate a.txt b.txt c.txt     여러 작품 집계 + 패턴 도출
    novel-factory serve                           API 서버 실행

LLM 없이 전부 동작한다.
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
