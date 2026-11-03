"""回放链接解析回归检查：python3 test_parse_url.py。"""
from pipeline import PipelineError, parse_url


def main():
    params = "course_id=87705&sub_id=1984223&tenant_code=112"
    expected = {"course_id": "87705", "sub_id": "1984223", "tenant_code": "112"}
    for base in (
        "https://classroom.zju.edu.cn/livingroom?",
        "https://interactivemeta.cmc.zju.edu.cn/#/replay?",
    ):
        assert parse_url(base + params) == expected
        assert parse_url(base + "sub_id=1984223") == {
            "course_id": "", "sub_id": "1984223", "tenant_code": "112"}
        assert parse_url(base + "tenant_code=113&sub_id=1984223&course_id=87705") == {
            **expected, "tenant_code": "113"}
        for invalid in ("course_id=87705", "sub_id=", "note=sub_id%3D1984223"):
            try:
                parse_url(base + invalid)
            except PipelineError:
                pass
            else:
                raise AssertionError(f"无效链接未被拒绝：{base + invalid}")
    print("回放链接解析检查通过")


if __name__ == "__main__":
    main()
