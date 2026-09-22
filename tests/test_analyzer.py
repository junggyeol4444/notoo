"""분석기와 파이프라인 테스트."""

from __future__ import annotations

import pytest

from novel_factory.reference.analyzer import (
    analyze_basic_stats,
    analyze_characters,
    analyze_cliffhangers,
    analyze_emotion,
    analyze_foreshadowing,
    analyze_pacing,
    analyze_relationships,
    analyze_style,
    classify_cliffhanger,
    compute_all,
)
from novel_factory.reference.analyzer.cliffhanger import NO_CLIFFHANGER
from novel_factory.reference.pattern import (
    ReferenceWeights,
    aggregate_profiles,
    derive_patterns,
)
from novel_factory.reference.profile import ReferenceProfile
from novel_factory.reference.similarity import FingerprintIndex, check_text
from novel_factory.text.lexicon import DEFAULT_LEXICON

#: 픽스처가 만든 합성 소설의 실제 등장인물
FIXTURE_CHARACTERS = {"김도윤", "박서연", "최민석", "강태호", "이준혁"}


@pytest.fixture(scope="module")
def metrics(sample_episodes):
    return compute_all(sample_episodes)


class TestEpisodeMetrics:
    def test_every_episode_measured(self, metrics, sample_episodes) -> None:
        assert len(metrics) == len(sample_episodes)

    def test_ratios_are_bounded(self, metrics) -> None:
        for m in metrics:
            assert 0.0 <= m.dialogue_ratio <= 1.0
            assert 0.0 <= m.narration_ratio <= 1.0
            assert -1.0 <= m.valence <= 1.0

    def test_valence_is_smoothed(self, metrics) -> None:
        """평활화가 없으면 부정어 한 번에 -1.0이 되어 곡선이 톱니가 된다."""
        extremes = [m for m in metrics if abs(m.valence) > 0.95]
        assert not extremes, [m.title for m in extremes]

    def test_short_episode_does_not_explode_rates(self, metrics) -> None:
        """짧은 프롤로그에서 1,000자당 비율이 폭발하면 안 된다."""
        assert max(m.event_score for m in metrics) < 100


class TestBasicStats:
    def test_totals(self, metrics, sample_episodes) -> None:
        stats = analyze_basic_stats(metrics)
        assert stats.episode_count == len(sample_episodes)
        assert stats.total_chars == sum(e.char_count for e in sample_episodes)
        assert stats.avg_sentence_chars > 0

    def test_speech_ratios_sum_to_one(self, metrics) -> None:
        stats = analyze_basic_stats(metrics)
        total = stats.dialogue_ratio + stats.inner_ratio + stats.narration_ratio
        assert total == pytest.approx(1.0, abs=0.01)

    def test_empty(self) -> None:
        assert analyze_basic_stats([]).episode_count == 0


class TestPacing:
    def test_intervals(self, metrics) -> None:
        pacing = analyze_pacing(metrics)
        assert pacing.first_event_episode is not None
        assert pacing.major_event_interval > 0
        assert pacing.minor_event_interval <= pacing.major_event_interval
        assert pacing.plot_speed in ("fast", "medium", "slow", "unknown")

    def test_climax_is_inside_the_work(self, metrics) -> None:
        pacing = analyze_pacing(metrics)
        assert 0.0 < (pacing.climax_position or 0.0) <= 1.0

    def test_flat_distribution_yields_no_events(self) -> None:
        """사건 강도가 전부 같으면 분위수 판정이 의미 없다."""
        from novel_factory.reference.analyzer.episode_metrics import compute_metrics
        from novel_factory.reference.structure.splitter import Episode

        flat = [
            Episode(index=i, number=i + 1, title=f"{i}", text="조용한 하루였다.", seq=i + 1)
            for i in range(5)
        ]
        pacing = analyze_pacing([compute_metrics(e) for e in flat])
        assert pacing.major_event_episodes == []


class TestCliffhanger:
    def test_fixture_hooks_are_detected(self, metrics) -> None:
        """픽스처는 거의 모든 회차를 훅으로 끝낸다."""
        profile = analyze_cliffhangers(metrics)
        assert profile.rate > 0.8

    def test_all_eight_types_are_reachable(self, metrics) -> None:
        profile = analyze_cliffhangers(metrics)
        assert len(profile.distribution) >= 6

    def test_structure_alone_is_not_a_hook(self) -> None:
        """짧은 마지막 문단만으로 클리프행어라고 하면 안 된다."""
        verdict = classify_cliffhanger("그는 자리에 앉았다.")
        assert verdict.kind == NO_CLIFFHANGER

    def test_no_false_positive_on_substring(self) -> None:
        """'왜곡'의 '왜'가 미스터리 신호로 잡히면 안 된다."""
        assert (
            classify_cliffhanger("실적이 왜곡되었다는 보고서였다.").kind == NO_CLIFFHANGER
        )

    def test_real_question_is_a_hook(self) -> None:
        assert classify_cliffhanger("그는 왜 여기 있는 걸까.").kind == "미스터리"

    def test_empty_tail(self) -> None:
        assert classify_cliffhanger("").kind == NO_CLIFFHANGER


class TestCharacters:
    def test_finds_exactly_the_real_cast(self, sample_episodes) -> None:
        """일반 명사·서술어가 인물로 잡히면 안 된다."""
        profile = analyze_characters(sample_episodes)
        found = {c.name for c in profile.characters}
        assert found == FIXTURE_CHARACTERS, found

    def test_protagonist_is_chosen(self, sample_episodes) -> None:
        profile = analyze_characters(sample_episodes)
        assert profile.protagonist_count == 1

    def test_appearance_stats(self, sample_episodes) -> None:
        profile = analyze_characters(sample_episodes)
        for c in profile.characters:
            assert c.first_episode <= c.last_episode
            assert c.episode_coverage >= 1

    def test_profile_hides_names_by_default(self, sample_episodes) -> None:
        """기획안 56번: 참고작의 고유명사는 저장하지 않는다."""
        payload = analyze_characters(sample_episodes).as_dict()
        assert "characters" not in payload
        serialized = str(payload)
        assert not any(name in serialized for name in FIXTURE_CHARACTERS)

    def test_empty_input(self) -> None:
        assert analyze_characters([]).characters == []


class TestRelationships:
    def test_tracks_pairs(self, sample_episodes) -> None:
        characters = analyze_characters(sample_episodes)
        profile = analyze_relationships(sample_episodes, characters)
        assert profile.tracks
        for track in profile.tracks:
            assert track.a != track.b

    def test_hides_names_by_default(self, sample_episodes) -> None:
        characters = analyze_characters(sample_episodes)
        payload = analyze_relationships(sample_episodes, characters).as_dict()
        assert "tracks" not in payload


class TestForeshadowing:
    def test_finds_planted_props(self, sample_episodes) -> None:
        """픽스처에 심어 둔 소재(수첩, 봉투)가 후보에 있어야 한다."""
        profile = analyze_foreshadowing(
            sample_episodes, stopwords=DEFAULT_LEXICON.name_stopwords
        )
        terms = {c.term for c in profile.candidates}
        assert terms & {"수첩", "봉투"}, terms

    def test_density_is_plausible(self, sample_episodes) -> None:
        """후보가 회차 수보다 훨씬 많으면 조건이 너무 느슨한 것이다."""
        profile = analyze_foreshadowing(
            sample_episodes, stopwords=DEFAULT_LEXICON.name_stopwords
        )
        assert len(profile.candidates) <= len(sample_episodes)

    def test_payoff_is_after_setup(self, sample_episodes) -> None:
        profile = analyze_foreshadowing(
            sample_episodes, stopwords=DEFAULT_LEXICON.name_stopwords
        )
        for c in profile.candidates:
            assert c.payoff_episode > c.setup_episode
            assert c.span >= 3

    def test_too_short_work(self) -> None:
        assert analyze_foreshadowing([]).candidates == []


class TestEmotionAndStyle:
    def test_emotion_curve_length(self, metrics) -> None:
        curve = analyze_emotion(metrics)
        assert len(curve.series) == len(metrics)
        assert len(curve.stages) == len(metrics)

    def test_stage_distribution_sums_to_one(self, metrics) -> None:
        curve = analyze_emotion(metrics)
        assert sum(curve.stage_distribution.values()) == pytest.approx(1.0)

    def test_pov_detected_from_narration(self, metrics, sample_episodes) -> None:
        style = analyze_style(metrics, sample_episodes)
        assert style.pov in ("1인칭", "3인칭", "혼합", "판정불가")

    def test_style_has_no_source_sentences(self, metrics, sample_episodes) -> None:
        """문체 프로파일에 원문 문장이 들어가면 안 된다."""
        payload = str(analyze_style(metrics, sample_episodes).as_dict())
        assert "서류를" not in payload


class TestPipeline:
    def test_profile_is_complete(self, sample_analysis) -> None:
        profile = sample_analysis.profile
        assert profile.basic and profile.pacing and profile.cliffhanger
        assert profile.characters and profile.emotion and profile.style

    def test_summary_keys(self, sample_analysis) -> None:
        summary = sample_analysis.profile.summary()
        for key in (
            "episode_count",
            "avg_episode_length",
            "dialogue_ratio",
            "cliffhanger_rate",
            "major_event_interval",
            "plot_speed",
        ):
            assert key in summary

    def test_report_is_human_readable(self, sample_analysis) -> None:
        report = sample_analysis.profile.report()
        assert "기본 통계" in report and "클리프행어" in report

    def test_stored_roundtrip(self, sample_analysis) -> None:
        stored = sample_analysis.profile.as_dict()
        restored = ReferenceProfile.from_stored(stored, reference_id="REF_TEST")
        assert restored.summary() == sample_analysis.profile.summary()

    def test_short_work_is_flagged(self) -> None:
        from novel_factory.reference import analyze_text

        text = "제1화\n\n본문이다.\n\n제2화\n\n본문이다.\n"
        result = analyze_text(text, reference_id="REF_SHORT")
        assert any("회차가" in w for w in result.profile.warnings)


class TestAggregation:
    def test_weights_can_exclude_an_aspect(self, sample_analysis) -> None:
        """강도 0을 주면 그 항목은 집계에서 빠진다 (기획안 16번)."""
        profile = sample_analysis.profile
        weights = {
            profile.reference_id: ReferenceWeights(
                profile.reference_id, weights={"style": 0.0}
            )
        }
        aggregated = aggregate_profiles([profile], weights)
        assert "avg_sentence_length" not in aggregated.metrics

    def test_target_band_is_a_range(self, sample_analysis) -> None:
        aggregated = aggregate_profiles([sample_analysis.profile])
        low, high = aggregated.target_band("major_event_interval")
        assert low < high

    def test_patterns_are_derived(self, sample_analysis) -> None:
        aggregated = aggregate_profiles([sample_analysis.profile])
        patterns = derive_patterns(aggregated)
        assert patterns
        assert all(p.instruction for p in patterns)


class TestSimilarity:
    @pytest.fixture
    def index(self, sample_analysis) -> FingerprintIndex:
        index = FingerprintIndex()
        index.add_from_metrics("REF_TEST", sample_analysis.metrics)
        return index

    def test_exact_copy_fails(self, index, sample_analysis) -> None:
        report = check_text(sample_analysis.episodes[5].text, index)
        assert report.verdict == "FAIL"

    def test_whitespace_stripped_copy_still_fails(self, index, sample_analysis) -> None:
        stripped = sample_analysis.episodes[5].text.replace(" ", "")
        assert check_text(stripped, index).verdict == "FAIL"

    def test_cross_episode_copy_fails(self, index, sample_txt) -> None:
        """회차 경계를 걸친 사본은 작품 전체 대조로만 잡힌다."""
        whole = sample_txt.read_text(encoding="utf-8")
        assert check_text(whole[3000:4500], index).verdict == "FAIL"

    def test_unrelated_text_passes(self, index) -> None:
        text = "빗줄기가 유리창을 두드렸다. 소년은 언덕을 올랐다. " * 40
        assert check_text(text, index).verdict == "PASS"

    def test_empty_index(self) -> None:
        assert check_text("아무 글", FingerprintIndex()).verdict == "PASS"
