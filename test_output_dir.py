"""输出命名与流水线回归检查：python3 test_output_dir.py。"""
import tempfile
from pathlib import Path
from unittest.mock import patch

import pipeline


def main():
    course = {"course_title": "物联网技术基础与应用开发", "class_begin": "1792560300"}
    url = "https://classroom.zju.edu.cn/livingroom?course_id=87705&sub_id=1984223"
    with tempfile.TemporaryDirectory() as root:
        with patch.object(pipeline, "fetch_course_info", return_value=course), \
                patch.object(pipeline, "fetch_transcript", return_value=[(0, "测试字幕")]), \
                patch.object(pipeline, "deepseek_chat", return_value=("测试总结", {})):
            transcript = pipeline.run_pipeline(url, out_root=root, mode="transcript")
            assert Path(transcript["result_dir"]).name == "物联网技术基础与应用开发-10.21"
            assert "测试字幕" in Path(transcript["transcript_path"]).read_text(encoding="utf-8")
            summary = pipeline.run_pipeline(url, out_root=root, api_key="test")
            assert Path(summary["result_dir"]).name == "物联网技术基础与应用开发-10.21（2）"
            assert "测试总结" in Path(summary["summary_path"]).read_text(encoding="utf-8")
            assert Path(transcript["transcript_path"]).exists()
        unsafe = {"course_title": "../课程:名称/测试.", "class_begin": "1792599300"}
        path = Path(pipeline.create_result_dir(root, unsafe, "1984223"))
        assert path.parent == Path(root)
        assert path.name == ".._课程_名称_测试-10.22"  # UTC 为 10.21，北京时间为 10.22
        with patch.object(pipeline, "fetch_course_info", side_effect=pipeline.PipelineError("离线")), \
                patch.object(pipeline, "fetch_transcript", return_value=[(0, "仍可下载")]):
            fallback = pipeline.run_pipeline(url, out_root=root, mode="transcript")
            assert Path(fallback["result_dir"]).name == "课程_sub1984223-日期未知"
        assert Path(pipeline.create_result_dir(root, {"class_begin": "invalid"}, "123")).name == \
            "课程_sub123-日期未知"
    print("输出目录检查通过")


if __name__ == "__main__":
    main()
