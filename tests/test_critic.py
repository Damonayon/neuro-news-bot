"""Тесты bot.critic — AI-критик с экспертной калибровкой."""

from __future__ import annotations

import json
from unittest.mock import patch

from bot.ai import LLMResponse, ModelTier
from bot.critic import (
    CRITERION_WEIGHTS,
    QUALITY_THRESHOLD,
    _calculate_overall,
    critique_post,
)


def _llm_response(payload: dict) -> LLMResponse:
    return LLMResponse(content=json.dumps(payload), model_used="gpt-4o-mini", tier=ModelTier.CRITIC)


class TestWeights:
    def test_weights_sum_to_one(self) -> None:
        assert abs(sum(CRITERION_WEIGHTS.values()) - 1.0) < 1e-9

    def test_hook_is_heaviest(self) -> None:
        """SMM-приоритет: первые 3 секунды решают."""
        assert CRITERION_WEIGHTS["hook"] == max(CRITERION_WEIGHTS.values())


class TestCalculateOverall:
    def test_all_tens(self) -> None:
        scores = dict.fromkeys(CRITERION_WEIGHTS, 10)
        assert _calculate_overall(scores) == 10

    def test_all_ones(self) -> None:
        scores = dict.fromkeys(CRITERION_WEIGHTS, 1)
        assert _calculate_overall(scores) == 1

    def test_clamped(self) -> None:
        """Защита от out-of-range значений модели."""
        scores = dict.fromkeys(CRITERION_WEIGHTS, 100)
        assert _calculate_overall(scores) == 10

    def test_low_hook_drags_down_overall(self) -> None:
        """Низкий hook должен заметно снижать overall, даже если остальное на 10."""
        scores = {
            "hook": 1,
            "specificity": 10,
            "value": 10,
            "emotion": 10,
            "grammar": 10,
            "originality": 10,
        }
        # weight hook=0.25, остальное даёт 7.5 → overall ≈ 8
        assert _calculate_overall(scores) <= 8


class TestCritiquePost:
    def test_approves_high_scores(self) -> None:
        resp = _llm_response(
            {
                "hook": 9,
                "specificity": 9,
                "value": 8,
                "emotion": 8,
                "grammar": 9,
                "originality": 8,
                "feedback": "",
            }
        )
        with patch("bot.critic.call_llm", return_value=resp):
            result = critique_post("очень хороший пост" * 50)
            assert result.approved
            assert result.overall >= QUALITY_THRESHOLD

    def test_rejects_low_scores(self) -> None:
        resp = _llm_response(
            {
                "hook": 3,
                "specificity": 4,
                "value": 4,
                "emotion": 3,
                "grammar": 7,
                "originality": 4,
                "feedback": "усиль крючок",
            }
        )
        with patch("bot.critic.call_llm", return_value=resp):
            result = critique_post("слабый пост")
            assert not result.approved
            assert result.verdict == "regenerate"
            assert "крючок" in result.feedback

    def test_overall_ignores_model_overall(self) -> None:
        """Даже если модель завысила overall, мы пересчитываем по своим весам."""
        # Модель ставит low scores, но возвращает overall=10 — мы должны проигнорировать
        resp = _llm_response(
            {
                "hook": 2,
                "specificity": 2,
                "value": 2,
                "emotion": 2,
                "grammar": 2,
                "originality": 2,
                "overall": 10,  # ← модель пытается обмануть
                "feedback": "",
            }
        )
        with patch("bot.critic.call_llm", return_value=resp):
            result = critique_post("post")
            assert result.overall == 2
            assert not result.approved

    def test_failsafe_on_critic_error(self) -> None:
        """Сбой критика не должен валить пайплайн — возвращаем нейтральный approve."""
        with patch("bot.critic.call_llm", side_effect=RuntimeError("LLM down")):
            result = critique_post("post")
            assert result.approved  # fail-safe
            assert result.feedback == "critic unavailable"

    def test_handles_missing_fields(self) -> None:
        """Если модель пропустила поля — заполняем нейтральным 5."""
        resp = _llm_response({"hook": 8, "feedback": ""})  # остальные поля отсутствуют
        with patch("bot.critic.call_llm", return_value=resp):
            result = critique_post("post")
            # hook=8 (вес 0.25 = 2.0) + остальное по 5 (вес 0.75 = 3.75) ≈ 6 → regenerate
            assert 5 <= result.overall <= 7

    def test_invalid_score_clamped(self) -> None:
        """Если модель вернула строку или out-of-range — clamp в [1, 10]."""
        resp = _llm_response(
            {
                "hook": "not-an-int",
                "specificity": 100,
                "value": -5,
                "emotion": 7,
                "grammar": 7,
                "originality": 7,
                "feedback": "",
            }
        )
        with patch("bot.critic.call_llm", return_value=resp):
            result = critique_post("post")
            assert result.scores["hook"] == 5  # дефолт при невалидном
            assert result.scores["specificity"] == 10  # clamp до 10
            assert result.scores["value"] == 1  # clamp до 1
