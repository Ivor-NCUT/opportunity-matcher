import unittest
from types import SimpleNamespace
from unittest.mock import patch

from opportunity_matcher.mail_classifier import ark_actionable_error_message, build_mail_classifier, inspect_mail_classifier


def completion(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class MailClassifierTest(unittest.TestCase):
    def test_ark_classifier_parses_json_response(self) -> None:
        with patch("opportunity_matcher.mail_classifier.ark_chat_completion", return_value=completion('{"label":"candidate","confidence":"high","reason":"投递简历"}')) as mocked:
            classifier = build_mail_classifier(model="ep-test", base_url="https://ark.example/api/v3", timeout=3)
            result = classifier({"subject": "投递简历", "from": "a@example.com", "body": "请看附件"})

        self.assertEqual(result.label, "candidate")
        self.assertEqual(result.provider, "ark")
        self.assertEqual(result.model, "ep-test")
        self.assertIn("投递简历", result.reason)
        mocked.assert_called_once()

    def test_inspect_ark_reports_missing_api_key(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            result = inspect_mail_classifier(model="ep-test", base_url="https://ark.example/api/v3", timeout=1)

        self.assertTrue(result["enabled"])
        self.assertFalse(result["available"])
        self.assertIn("ARK_API_KEY", result["reason"])

    def test_inspect_ark_reports_available_after_healthcheck(self) -> None:
        with patch("opportunity_matcher.mail_classifier.ark_chat_completion", return_value=completion("OK")):
            result = inspect_mail_classifier(model="ep-test", base_url="https://ark.example/api/v3", timeout=1)

        self.assertTrue(result["available"])
        self.assertEqual(result["provider"], "ark")

    def test_no_available_model_error_is_actionable(self) -> None:
        message = ark_actionable_error_message(
            RuntimeError("Error code: 404 - {'error': {'code': 'NoAvailableModel'}}"),
            model="ep-test",
            base_url="https://ark.example/api/v3",
        )

        self.assertIn("no available model instance", message)
        self.assertIn("ep-test", message)
        self.assertIn("Ark console", message)


if __name__ == "__main__":
    unittest.main()
