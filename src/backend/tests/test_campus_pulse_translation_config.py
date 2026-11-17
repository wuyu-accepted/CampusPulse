from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from backend.campus_pulse.provider_config import save_provider_config
from backend.campus_pulse.translation import _credential, _endpoint, _models


class CampusPulseTranslationConfigTests(TestCase):
    def test_translation_reuses_machine_local_provider_configuration(self) -> None:
        with TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {
                    "CAMPUS_PULSE_PROVIDER_CONFIG_DIR": directory,
                    "CAMPUS_PULSE_TRANSLATION_ENDPOINT": "",
                    "CAMPUS_PULSE_TRANSLATION_MODELS": "",
                    "CAMPUS_PULSE_TRANSLATION_API_KEY_FILE": "",
                    "YUNWU_API_KEY": "",
                },
                clear=False,
            ):
                save_provider_config(
                    base_url="https://provider.example/v1",
                    model_name="translation-capable-model",
                    reasoning_effort="none",
                    temperature=0,
                    max_output_tokens=512,
                    timeout_seconds=45,
                    max_concurrency=2,
                    api_key="test-secret-that-must-not-be-returned-by-status",
                )
                self.assertEqual(_endpoint(), "https://provider.example/v1/chat/completions")
                self.assertEqual(_models(), ["translation-capable-model"])
                self.assertTrue(_credential())
                self.assertNotIn(
                    "test-secret",
                    Path(directory, "provider-config.json").read_text(encoding="utf-8"),
                )

