import unittest

from reuse_title import validate_page_crop, validate_page_identity, validate_receipt


class TitleReuseTests(unittest.TestCase):
    def setUp(self):
        self.review = {"task": 75, "exact_match": True, "request_id": "request-75",
                       "transcribed": "AUM12P-AF05/2AM03-010TL"}
        self.call = {"project_id": 1, "phase": "read", "usage_ceiling_cny": "0.0019",
                     "result": '{"sent":true,"actual_model":"deepseek-flash","http_status":200,'
                               '"finish_reason":"completed","error_type":null,"request_id":"request-75",'
                               '"response":"AUM12P-AF05/2AM03-010TL","usage":{"input_tokens":883}}'}
        self.pages = [{"media_box": [0, 0, 1684, 1191], "page_sha256": "a" * 64}]

    def test_accepts_metered_exact_receipt(self):
        result = validate_receipt(self.call, self.review, 75, 1, "AUM12P-AF05/2AM03-010TL")
        self.assertEqual(result["request_id"], "request-75")

    def test_rejects_wrong_project(self):
        with self.assertRaisesRegex(ValueError, "项目或阶段"):
            validate_receipt({**self.call, "project_id": 2}, self.review, 75, 1, "AUM12P-AF05/2AM03-010TL")

    def test_rejects_nonexact_or_wrong_model_receipts(self):
        with self.assertRaisesRegex(ValueError, "精确匹配"):
            validate_receipt(self.call, {**self.review, "exact_match": False}, 75, 1, "AUM12P-AF05/2AM03-010TL")
        changed = {**self.call, "result": self.call["result"].replace("deepseek-flash", "qa-simulated")}
        with self.assertRaisesRegex(ValueError, "成功且已计量"):
            validate_receipt(changed, self.review, 75, 1, "AUM12P-AF05/2AM03-010TL")

    def test_rejects_unknown_or_unmetered_receipt(self):
        with self.assertRaisesRegex(ValueError, "成功且已计量"):
            validate_receipt({**self.call, "usage_ceiling_cny": None}, self.review, 75, 1,
                              "AUM12P-AF05/2AM03-010TL")
        with self.assertRaisesRegex(ValueError, "冻结目标料号"):
            validate_receipt(self.call, self.review, 75, 1, "AUM12P-AF05/2AM03-01OTL")

    def test_requires_all_pages_and_pixel_identity(self):
        self.assertEqual(validate_page_identity(self.pages, self.pages), self.pages)
        with self.assertRaisesRegex(ValueError, "页数"):
            validate_page_identity(self.pages, self.pages + self.pages)
        with self.assertRaisesRegex(ValueError, "页数"):
            validate_page_identity(self.pages, [{**self.pages[0], "page_sha256": "b" * 64}])

    def test_requires_frozen_target_crop_hash(self):
        self.assertEqual(validate_page_crop(b"crop", "375676bd26868505668fce072799a6e029a37fbbe67b70b89f7b68def282344c"), b"crop")
        with self.assertRaisesRegex(ValueError, "裁片 SHA-256"):
            validate_page_crop(b"crop", "0" * 64)


if __name__ == "__main__":
    unittest.main()
