"""Narration units: where the voice breathes, slows down and speeds up."""

import unittest

from video_generator.domain.prosody import (
    BEAT_MAX_CHARS,
    BEAT_MAX_SENTENCES,
    PAUSE_AFTER_BEAT,
    PAUSE_BEFORE_BEAT,
    PAUSE_PARAGRAPH,
    PAUSE_SENTENCE,
    SPEED_BEAT,
    SPEED_NORMAL,
    SPEED_RUN,
    NarrationUnit,
    ProsodyError,
    plan_narration_units,
)


class NarrationUnitContractTests(unittest.TestCase):
    def test_rejects_empty_untrimmed_and_out_of_range_values(self):
        for kwargs in (
            {"text": "   "},
            {"text": " padded "},
            {"speed": 0.1},
            {"speed": 9.0},
            {"speed": True},
            {"pause_after_seconds": -0.1},
            {"pause_after_seconds": 99.0},
            {"role": "chorus"},
        ):
            base = {"text": "Uma frase.", "speed": 1.0, "pause_after_seconds": 0.3, "role": "line"}
            base.update(kwargs)
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ProsodyError):
                    NarrationUnit(**base)

    def test_to_dict_rounds_for_persistence(self):
        unit = NarrationUnit(text="Frase.", speed=2.0 / 3, pause_after_seconds=0.123456, role="line")
        self.assertEqual(
            unit.to_dict(),
            {"text": "Frase.", "speed": 0.667, "pause_after_seconds": 0.123, "role": "line"},
        )


class PlanNarrationUnitsTests(unittest.TestCase):
    def test_rejects_scripts_with_nothing_to_say(self):
        for text in ("", "   ", "\n\n\n", None, 7):
            with self.subTest(text=text):
                with self.assertRaises(ProsodyError):
                    plan_narration_units(text)

    def test_every_word_of_the_script_survives_in_order(self):
        script = (
            "Primeira frase longa o suficiente para nao virar um beat curto.\n\n"
            "Curta. Outra. Mais uma.\n\n"
            "Fim."
        )
        units = plan_narration_units(script)
        self.assertEqual(
            " ".join(unit.text for unit in units).split(),
            script.split(),
        )

    def test_a_short_standalone_paragraph_is_a_slow_beat(self):
        script = (
            "Uma frase de abertura suficientemente longa para nao ser um beat.\n\n"
            "Mas parece verdade.\n\n"
            "E a narracao continua depois com outra frase bem longa aqui."
        )
        units = plan_narration_units(script)
        beat = units[1]
        self.assertEqual(beat.role, "beat")
        self.assertEqual(beat.speed, SPEED_BEAT)
        self.assertLessEqual(len(beat.text), BEAT_MAX_CHARS)
        self.assertEqual(beat.pause_after_seconds, PAUSE_AFTER_BEAT)

    def test_silence_is_placed_before_a_beat_as_well_as_after(self):
        script = (
            "Uma frase de abertura suficientemente longa para nao ser um beat.\n\n"
            "Mas parece verdade.\n\n"
            "E a narracao continua depois com outra frase bem longa aqui."
        )
        units = plan_narration_units(script)
        self.assertEqual(units[0].pause_after_seconds, PAUSE_BEFORE_BEAT)
        self.assertGreater(PAUSE_BEFORE_BEAT, PAUSE_PARAGRAPH)

    def test_short_consecutive_sentences_become_one_faster_run(self):
        script = (
            "Estudou. Tem carreira. Resolve problemas.\n\n"
            "Depois vem uma frase longa que fecha o paragrafo com calma total."
        )
        units = plan_narration_units(script)
        run = units[0]
        self.assertEqual(run.role, "run")
        self.assertEqual(run.speed, SPEED_RUN)
        self.assertEqual(run.text, "Estudou. Tem carreira. Resolve problemas.")


    def test_a_short_pair_of_sentences_is_still_a_beat_but_a_list_is_not(self):
        opening = "Uma frase de abertura suficientemente longa para nao ser um beat."
        closing = "E a narracao continua depois com outra frase bem longa aqui."
        pair = plan_narration_units(
            f"{opening}\n\nA informacao e a mesma. O cerebro nao.\n\n{closing}"
        )[1]
        self.assertEqual(pair.role, "beat")
        self.assertEqual(BEAT_MAX_SENTENCES, 2)
        listing = plan_narration_units(
            f"{opening}\n\nEstudou. Tem carreira. Resolve.\n\n{closing}"
        )[1]
        self.assertEqual(listing.role, "run")
        self.assertEqual(listing.speed, SPEED_RUN)

    def test_a_pause_inside_a_paragraph_is_shorter_than_at_its_end(self):
        script = (
            "Uma primeira frase longa que ocupa bastante espaco no paragrafo. "
            "Uma segunda frase longa que tambem ocupa bastante espaco aqui.\n\n"
            "Um paragrafo final com uma frase longa o suficiente para nao virar beat."
        )
        units = plan_narration_units(script)
        self.assertEqual(units[0].pause_after_seconds, PAUSE_SENTENCE)
        self.assertEqual(units[1].pause_after_seconds, PAUSE_PARAGRAPH)
        self.assertEqual(units[0].speed, SPEED_NORMAL)

    def test_a_question_holds_longer_than_a_statement(self):
        statement = plan_narration_units(
            "Uma frase longa que termina em ponto e ocupa espaco suficiente.\n\nOutra frase longa qualquer aqui."
        )[0]
        question = plan_narration_units(
            "Uma frase longa que termina em interrogacao e ocupa espaco assim?\n\nOutra frase longa qualquer aqui."
        )[0]
        self.assertGreater(question.pause_after_seconds, statement.pause_after_seconds)

    def test_nothing_hangs_after_the_last_word(self):
        units = plan_narration_units("Primeira frase bem longa para o teste.\n\nDesumanizando.")
        self.assertEqual(units[-1].pause_after_seconds, 0.0)

    def test_planning_is_deterministic(self):
        script = "Uma frase.\n\nOutra frase um pouco mais longa para variar o ritmo.\n\nFim."
        self.assertEqual(plan_narration_units(script), plan_narration_units(script))


if __name__ == "__main__":
    unittest.main()
