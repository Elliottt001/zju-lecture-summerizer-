# -*- coding: utf-8 -*-
"""
智云课堂回放 → 课程知识总结 流水线
只用 Python 标准库。核心输入是语音字幕（公开接口），可选增强是登录 Cookie 抓 PPT。
"""
import json
import os
import re
import shutil
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))


def _make_ssl_context():
    """macOS 官方 Python 常缺根证书：优先 certifi，其次系统默认，最后退化为不校验
    （字幕接口为公开只读数据，退化可接受，但会打印提示）。"""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    ctx = ssl.create_default_context()
    try:
        import socket
        with socket.create_connection(("classroom.zju.edu.cn", 443), 10) as sock:
            # 必须 wrap 这个已连接的 sock 才会真正握手；握手成功才说明证书可用
            with ctx.wrap_socket(sock, server_hostname="classroom.zju.edu.cn"):
                return ctx
    except Exception:
        pass
    print("提示：本机 Python 缺少根证书，已退化为不校验 HTTPS 证书。"
          "建议运行一次 /Applications/Python 3.13/Install Certificates.command")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


SSL_CTX = _make_ssl_context()

BASE = "https://classroom.zju.edu.cn"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")


class PipelineError(Exception):
    pass


def _log(progress, msg):
    if progress:
        progress(msg)
    else:
        print(msg)


# ---------------------------------------------------------------- URL 解析

def parse_url(url):
    """从 livingroom 回放 URL 里提取 course_id / sub_id / tenant_code。"""
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    except Exception:
        raise PipelineError("URL 无法解析，请粘贴浏览器地址栏的完整链接")
    sub_id = q.get("sub_id", [None])[0]
    if not sub_id:
        raise PipelineError("URL 里找不到 sub_id 参数，请确认是 livingroom 回放页地址")
    return {
        "course_id": q.get("course_id", [""])[0],
        "sub_id": sub_id,
        "tenant_code": q.get("tenant_code", ["112"])[0],
    }


# ---------------------------------------------------------------- 字幕抓取

def fetch_transcript(sub_id, tenant_code="112", timeout=60):
    """公开接口，无需登录。返回 [(BeginSec, Text), ...] 按时间排序。"""
    url = (f"{BASE}/courseapi/v3/web-socket/search-trans-result"
           f"?sub_id={sub_id}&tenant_code={tenant_code}")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                                context=SSL_CTX) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise PipelineError(f"字幕接口返回 HTTP {e.code}")
    except Exception as e:
        raise PipelineError(f"字幕接口请求失败：{e}")
    if data.get("code") != 0 or not data.get("list"):
        raise PipelineError(f"字幕接口无数据（code={data.get('code')}），"
                            "可能直播尚未结束或字幕未生成")
    segs = data["list"][0].get("all_content") or []
    segs = [(int(s["BeginSec"]), (s.get("Text") or "").strip())
            for s in segs if (s.get("Text") or "").strip()]
    segs.sort(key=lambda x: x[0])
    if not segs:
        raise PipelineError("字幕内容为空")
    return segs


def fmt_ts(sec):
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def build_transcript_texts(segs):
    """生成逐段版和按分钟合并版两份文本。"""
    per_seg = "\n".join(f"[{fmt_ts(t)}] {txt}" for t, txt in segs)
    minutes = {}
    for t, txt in segs:
        minutes.setdefault(t // 60, []).append(txt)
    per_min = "\n".join(f"[{m // 60:02d}:{m % 60:02d}] " + "".join(v)
                        for m, v in sorted(minutes.items()))
    return per_seg, per_min, minutes


# ---------------------------------------------------------------- 可选：PPT

def try_fetch_ppt_text(sub_id, tenant_code, cookie, timeout=30):
    """带 Cookie 尝试抓平台 PPT 文字层。失败返回 None（不影响主流程）。"""
    if not cookie:
        return None
    url = (f"{BASE}/pptnote/v1/schedule/search-ppt"
           f"?sub_id={sub_id}&tenant_code={tenant_code}")
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Cookie": cookie,
        "Referer": f"{BASE}/livingroom?sub_id={sub_id}",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                                context=SSL_CTX) as r:
            data = json.loads(r.read().decode("utf-8"))
        if data.get("code") == 0 and data.get("list"):
            return data["list"]
    except Exception:
        pass
    return None


# ---------------------------------------------------------------- ffmpeg

def ffmpeg_path():
    """优先用工具自带 bin/ffmpeg（界面下载来的），其次系统 PATH。"""
    local = os.path.join(HERE, "bin",
                         "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if os.path.isfile(local):
        return local
    return shutil.which("ffmpeg")


def _ffmpeg_url():
    """eugeneware/ffmpeg-static 的单文件静态构建（gzip 压缩）。"""
    import platform
    base = ("https://github.com/eugeneware/ffmpeg-static/releases/"
            "download/b6.0/")
    if os.name == "nt":
        return base + "ffmpeg-win32-x64.gz"
    arch = "arm64" if platform.machine() == "arm64" else "x64"
    return base + f"ffmpeg-darwin-{arch}.gz"


def download_ffmpeg(progress=None):
    """下载静态 ffmpeg 到 ./bin/（约 20–25MB）。返回可执行文件路径。"""
    import gzip
    url = _ffmpeg_url()
    bin_dir = os.path.join(HERE, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    want = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    target = os.path.join(bin_dir, want)
    gzpath = target + ".gz"
    _log(progress, f"开始下载 ffmpeg：{url}")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=900, context=SSL_CTX) as r:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            with open(gzpath, "wb") as f:
                while True:
                    buf = r.read(1 << 16)
                    if not buf:
                        break
                    f.write(buf)
                    done += len(buf)
                    if progress and total:
                        progress(f"下载 ffmpeg：{done // (1 << 20)} / "
                                 f"{total // (1 << 20)} MB")
        _log(progress, "解压中…")
        with gzip.open(gzpath, "rb") as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
    except Exception as e:
        raise PipelineError(f"ffmpeg 下载失败：{e}")
    finally:
        try:
            os.unlink(gzpath)
        except OSError:
            pass
    if os.name != "nt":
        os.chmod(target, 0o755)
    _log(progress, f"ffmpeg 已就绪：{target}")
    return target


# ------------------------------------------------------- 视频与增强识别

def _find_mp4(obj):
    """在 get-config 的 JSON 里递归找第一个 mp4 直链。"""
    if isinstance(obj, str):
        if obj.startswith("http") and ".mp4" in obj.split("?")[0]:
            return obj
        return None
    if isinstance(obj, dict):
        for v in obj.values():
            r = _find_mp4(v)
            if r:
                return r
    if isinstance(obj, list):
        for v in obj:
            r = _find_mp4(v)
            if r:
                return r
    return None


def fetch_video_url(course_id, sub_id, tenant_code, cookie, timeout=30):
    """带 Cookie 调 get-config，递归找出回放 mp4 直链。失败返回 None。"""
    if not cookie:
        return None
    url = (f"{BASE}/courseapi/v2/play-template/get-config"
           f"?course_id={course_id}&sub_id={sub_id}&tenant_code={tenant_code}")
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Cookie": cookie,
        "Referer": f"{BASE}/livingroom?sub_id={sub_id}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=SSL_CTX) as r:
            data = json.loads(r.read().decode("utf-8"))
        return _find_mp4(data)
    except Exception:
        return None


def download_file(url, dest, progress=None, timeout=1800):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as f:
            while True:
                buf = r.read(1 << 18)
                if not buf:
                    break
                f.write(buf)
                done += len(buf)
                if progress and total and done % (1 << 22) < (1 << 18):
                    progress(f"下载视频：{done // (1 << 20)} / "
                             f"{total // (1 << 20)} MB")
    return done


WHISPER_PROMPT = ("以下是浙江大学课程的中文授课录音，内容涉及算法与数据结构，"
                  "常见术语如：贪婪算法、贪心、动态规划、Huffman 编码、哈夫曼、"
                  "NP 完全、多项式规约、图灵机、停机问题、最小生成树、Dijkstra。")


def whisper_transcribe(audio_path, progress=None):
    """用 faster-whisper 重新识别音频，返回 [(BeginSec, Text), ...]。"""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise PipelineError(
            "增强识别模式需要 faster-whisper。请在终端运行一次："
            "pip install faster-whisper（约几百 MB，含模型自动下载）")
    _log(progress, "加载 Whisper large-v3 模型（首次会自动下载约 3GB）…")
    model = WhisperModel("large-v3", device="auto", compute_type="auto")
    _log(progress, "开始识别音频（较慢，耐心等）…")
    segments, _info = model.transcribe(
        audio_path, language="zh", initial_prompt=WHISPER_PROMPT,
        vad_filter=True)
    segs, n = [], 0
    for s in segments:
        txt = (s.text or "").strip()
        if txt:
            segs.append((int(s.start), txt))
        n += 1
        if progress and n % 50 == 0:
            progress(f"已识别 {n} 句，当前进度 {int(s.start) // 60} 分钟")
    if not segs:
        raise PipelineError("Whisper 没有识别出任何内容")
    return segs


def extract_audio(ffmpeg, video_path, audio_path, progress=None):
    _log(progress, "用 ffmpeg 抽取音频（16kHz 单声道 mp3）…")
    r = subprocess.run(
        [ffmpeg, "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000",
         "-b:a", "32k", audio_path],
        capture_output=True, text=True, timeout=3600)
    if r.returncode != 0 or not os.path.isfile(audio_path):
        raise PipelineError("ffmpeg 抽音频失败：" + (r.stderr or "")[-300:])


# ---------------------------------------------------------------- 分块

def chunk_minutes(minutes, max_chars=11000):
    """按分钟对齐切块，每块不超过 max_chars。返回 [(起分, 止分, 文本)]。"""
    chunks, cur, cur_start, cur_len = [], [], None, 0
    for m, texts in sorted(minutes.items()):
        line = f"[{m // 60:02d}:{m % 60:02d}] " + "".join(texts)
        if cur and cur_len + len(line) > max_chars:
            chunks.append((cur_start, cur[-1][0], "\n".join(x[1] for x in cur)))
            cur, cur_start, cur_len = [], None, 0
        if cur_start is None:
            cur_start = m
        cur.append((m, line))
        cur_len += len(line)
    if cur:
        chunks.append((cur_start, cur[-1][0], "\n".join(x[1] for x in cur)))
    return chunks


# ---------------------------------------------------------------- DeepSeek

def deepseek_chat(api_key, system, user, model="deepseek-chat",
                  max_tokens=4096, retries=5, temperature=0.3):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }).encode("utf-8")
    last_err = None
    for i in range(retries):
        req = urllib.request.Request(DEEPSEEK_URL, data=body, method="POST",
                                     headers={
                                         "Content-Type": "application/json",
                                         "Authorization": f"Bearer {api_key}",
                                         "User-Agent": UA,
                                     })
        try:
            with urllib.request.urlopen(req, timeout=300,
                                                context=SSL_CTX) as r:
                resp = json.loads(r.read().decode("utf-8"))
            content = resp["choices"][0]["message"]["content"]
            usage = resp.get("usage", {})
            return content, usage
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8")[:200]
            except Exception:
                pass
            last_err = f"HTTP {e.code} {detail}"
            if e.code == 401:
                raise PipelineError("DeepSeek API key 无效（401），请检查后重填")
            if e.code == 402:
                raise PipelineError("DeepSeek 账户余额不足（402），请充值后重试")
            if e.code in (429, 500, 502, 503):
                time.sleep(3 * (i + 1))   # 限流/服务端抖动，退避重试
                continue
            raise PipelineError(f"DeepSeek 调用失败：{last_err}")
        except PipelineError:
            raise
        except Exception as e:
            last_err = str(e)
            time.sleep(3 * (i + 1))
    raise PipelineError(f"DeepSeek 多次重试仍失败：{last_err}")


# ---------------------------------------------------------------- 课件

def extract_courseware(path):
    """从课件文件提取纯文字。支持 pptx / docx / txt / md。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".txt", ".md"):
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    if ext == ".pptx":
        with zipfile.ZipFile(path) as z:
            slides = sorted(
                (n for n in z.namelist()
                 if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                key=lambda n: int(re.search(r"(\d+)", n).group(1)))
            parts = []
            for n in slides:
                xml = z.read(n).decode("utf-8", "ignore")
                t = " ".join(re.findall(r"<a:t>([^<]*)</a:t>", xml))
                if t.strip():
                    parts.append(t)
            return "\n".join(parts)
    if ext == ".docx":
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
            return "\n".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))
    raise PipelineError(
        f"不支持的课件格式 {ext or '（无扩展名）'}，目前支持 pptx / docx / txt / md")


SUMMARY_SYSTEM = (
    "你是顶尖的大学课程笔记编辑，擅长从冗长的课堂口语中提炼知识骨架、抓住重点。"
    "用户给出一整节课的字幕（每分钟一行，行首 [时:分] 时间戳；语音识别可能有错别字，"
    "请结合上下文纠正，专业术语以课件/常识为准）。输出 Markdown，严格按以下结构：\n"
    "## 一、本课速览\n"
    "先用一张表列出各大板块：时间段 | 主题 | 一句话核心。再用 3–5 条列出"
    "『这节课最该带走的结论』——只留精华，让没听课的人 30 秒抓住主线。\n"
    "## 二、分章详解\n"
    "按知识板块组织（不按时间流水账）。每个板块依次写清：\n"
    "① 定义与结论（措辞精确，可直接背诵）；\n"
    "② 推导或证明思路（一步一步，不跳步，尽量配一个具体数字例子）；\n"
    "③ 例题（题目→解法→答案→易错点）；\n"
    "④ 老师特别强调、反复提及或提醒『会考/注意』的内容，单独标出。\n"
    "## 三、公式与算法清单\n"
    "所有关键公式用 LaTeX（除法写上下分式 \\frac{a}{b}，不要 a/b 斜杠式），"
    "算法给伪代码或步骤列表，注明复杂度。\n"
    "## 四、考试与作业信息\n"
    "老师提到的考试时间、范围、形式、作业、点名政策等，原样汇总；没有就写『无』。\n"
    "## 五、最可能考的点\n"
    "5–10 条，每条附一句理由。\n"
    "总要求：主线与闲聊严格区分——点名、设备调试、闲谈一律丢弃；"
    "老师重复或加重语气的内容必须体现；关键处标注来源时间戳 [时:分]；"
    "直接输出正文，不要任何客套话。"
)


CHUNK_SYSTEM = (
    "你是大学课堂笔记整理者。用户会给你一节课某个时段的语音识别字幕"
    "（每分钟一行，行首是 [时:分] 时间戳，识别可能有错别字，需按上下文纠正理解）。"
    "请提取该时段的课程内容，输出 Markdown，要求：\n"
    "1. 按主题组织知识点：定义、定理、公式、算法步骤、伪代码；\n"
    "2. 老师讲的例题：题目、解法、答案、易错点；\n"
    "3. 证明题写出证明思路，分步骤，不要跳步；\n"
    "4. 老师口头强调的考点、提醒、通知（如考试范围/时间）单独列出；\n"
    "5. 闲聊/点名/设备调试等与课程无关的内容直接忽略；\n"
    "6. 关键内容标注来源时间戳 [时:分]，方便回看视频；\n"
    "7. 数学公式用 LaTeX（$...$），除法写成上下分式 \\frac{a}{b}，不要 a/b 斜杠式。\n"
    "直接输出笔记内容，不要输出『好的』『以下是』之类的客套话。"
)

MERGE_SYSTEM = (
    "你是大学课程讲义编辑。用户会给你同一节课按时间顺序分段整理的笔记，"
    "请合并成一份完整、连贯的课程知识总结（Markdown），要求：\n"
    "1. 开头给出本课结构总览（讲了哪几大块、各对应时间段）；\n"
    "2. 按章节重新组织，去除分段之间的重复内容，保留所有时间戳标注；\n"
    "3. 定义、定理、公式、算法、例题、证明思路一个都不要丢；\n"
    "4. 老师强调的考点和通知（考试时间/范围等）汇总成单独一节放在显眼位置；\n"
    "5. 结尾给一个『最容易考的点』清单；\n"
    "6. 数学公式用 LaTeX，除法写成上下分式 \\frac{a}{b}。\n"
    "直接输出总结全文。"
)


# ---------------------------------------------------------------- 主流程

def run_pipeline(url, api_key=None, cookie=None, out_root=None, progress=None,
                 mode="summarize", video_url=None,
                 summary_system=None, courseware_text=None,
                 extra_requirements=None):
    """完整流水线。mode: transcript=只抓字幕 / summarize=字幕+总结 /
    enhanced=下载视频→Whisper 重识别→总结。
    总结默认单次请求（一致性好）；超出模型上下文时自动退化为顺序分块+合并。
    courseware_text: 上传课件提取的文字，作为术语与结构参考。
    extra_requirements: 用户自定义的额外要求，附加在请求末尾。"""
    summary_system = summary_system or SUMMARY_SYSTEM
    out_root = out_root or os.path.join(HERE, "output")
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0}

    _log(progress, "① 解析回放地址…")
    info = parse_url(url)
    sub_id, tenant = info["sub_id"], info["tenant_code"]
    _log(progress, f"   course_id={info['course_id']}  sub_id={sub_id}")

    result_dir = os.path.join(
        out_root, f"{datetime.now():%Y%m%d}_sub{sub_id}")
    raw_dir = os.path.join(result_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    if mode == "enhanced":
        _log(progress, "② 增强识别模式：准备视频与音频…")
        ff = ffmpeg_path()
        if not ff:
            raise PipelineError(
                "未找到 ffmpeg：请先在界面点「下载 ffmpeg」，或自行安装")
        vurl = video_url or fetch_video_url(
            info["course_id"], sub_id, tenant, cookie)
        if not vurl:
            raise PipelineError(
                "拿不到视频直链：请在界面粘贴 mp4 地址，或填写有效 Cookie")
        video_path = os.path.join(raw_dir, "video.mp4")
        if not os.path.isfile(video_path):
            _log(progress, "   下载回放视频（可能 1–2GB，耐心等）…")
            size = download_file(vurl, video_path, progress)
            _log(progress, f"   视频下载完成：{size // (1 << 20)} MB")
        else:
            _log(progress, "   检测到已下载的视频，跳过重复下载")
        audio_path = os.path.join(raw_dir, "audio.mp3")
        if not os.path.isfile(audio_path):
            extract_audio(ff, video_path, audio_path, progress)
        segs = whisper_transcribe(audio_path, progress)
        src_note = "Whisper large-v3 重新识别"
    else:
        _log(progress, "② 抓取平台语音字幕（公开接口，无需登录）…")
        segs = fetch_transcript(sub_id, tenant)
        src_note = "平台字幕"

    per_seg, per_min, minutes = build_transcript_texts(segs)
    dur = fmt_ts(segs[-1][0])
    _log(progress, f"   共 {len(segs)} 段 / {len(per_seg)} 字，课程时长约 {dur}"
                   f"（来源：{src_note}）")
    with open(os.path.join(raw_dir, "transcript.txt"), "w",
              encoding="utf-8") as f:
        f.write(per_seg)
    with open(os.path.join(raw_dir, "transcript_by_minute.txt"), "w",
              encoding="utf-8") as f:
        f.write(per_min)

    ppt = None
    if cookie and mode != "transcript":
        _log(progress, "③ 尝试用 Cookie 抓取 PPT 文字层（可选增强）…")
        ppt = try_fetch_ppt_text(sub_id, tenant, cookie)
        if ppt:
            with open(os.path.join(raw_dir, "ppt.json"), "w",
                      encoding="utf-8") as f:
                json.dump(ppt, f, ensure_ascii=False, indent=1)
            _log(progress, f"   PPT 数据已保存（{len(ppt)} 条）")
        else:
            _log(progress, "   PPT 抓取失败或课程未提供，回退为纯字幕总结")
    else:
        _log(progress, "③ 跳过 PPT 抓取")

    if mode == "transcript" or not api_key:
        if mode != "transcript":
            _log(progress, "注意：未填 DeepSeek API key，本次只抓取不总结。")
        _log(progress, f"字幕已保存到：{raw_dir}")
        return {"result_dir": result_dir, "summary_path": None,
                "transcript_path": os.path.join(raw_dir, "transcript.txt"),
                "segments": len(segs), "duration": dur, "usage": total_usage}

    ref = ""
    if courseware_text:
        ref = ("\n\n[参考课件文字（节选，用于校准术语与章节结构）]\n"
               + courseware_text[:6000])
    extra = ""
    if extra_requirements:
        extra = ("\n\n[用户的额外要求（必须满足，优先级高于上述默认结构）]\n"
                 + extra_requirements)
    meta = (f"课程：智云课堂回放 sub_id={sub_id}"
            f"（course_id={info['course_id']}），时长约 {dur}")
    user_full = f"{meta}{ref}\n\n本课完整字幕如下：\n\n{per_min}{extra}"

    _log(progress, "④ 调用 DeepSeek 生成完整讲义（单次请求）…")
    try:
        final, usage = deepseek_chat(api_key, summary_system, user_full,
                                     max_tokens=8192)
        total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
        total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
    except PipelineError as e:
        if "HTTP 400" not in str(e):
            raise
        _log(progress, "   单次请求超出模型上下文，退化为顺序分块处理（不并行）…")
        final = _summarize_chunked(
            api_key, minutes, os.path.join(result_dir, "分段笔记"),
            progress, total_usage)

    header = (f"# 课程知识总结\n\n"
              f"> 来源：智云课堂回放 sub_id={sub_id}"
              f"（course_id={info['course_id']}），时长约 {dur}\n"
              f"> 字幕来源：{src_note}"
              + ("；已参考上传课件" if courseware_text else "") + "\n"
              f"> 生成时间：{datetime.now():%Y-%m-%d %H:%M}\n"
              f"> token 消耗：输入 {total_usage['prompt_tokens']}，"
              f"输出 {total_usage['completion_tokens']}\n\n---\n\n")
    summary_path = os.path.join(result_dir, "知识总结.md")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(header + final)
    _log(progress, f"⑤ 完成。总结已生成，可在界面下载：{summary_path}")
    return {"result_dir": result_dir, "summary_path": summary_path,
            "transcript_path": os.path.join(raw_dir, "transcript.txt"),
            "segments": len(segs), "duration": dur, "usage": total_usage}


def _summarize_chunked(api_key, minutes, notes_dir, progress, total_usage):
    """兜底：顺序分块总结 + 合并（不并行，保证与单次请求同样的确定性顺序）。"""
    chunks = chunk_minutes(minutes)
    _log(progress, f"   切成 {len(chunks)} 块，逐块总结…")
    os.makedirs(notes_dir, exist_ok=True)
    notes = []
    for idx, (m0, m1, text) in enumerate(chunks):
        span = (f"{m0 // 60:02d}:{m0 % 60:02d}"
                f"–{m1 // 60:02d}:{(m1 + 1) % 60:02d}")
        user = f"课程时段 {span} 的字幕如下：\n\n{text}"
        content, usage = deepseek_chat(api_key, CHUNK_SYSTEM, user)
        total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
        total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
        fn = os.path.join(notes_dir,
                          f"{idx + 1:02d}_{span.replace(':', '')}.md")
        with open(fn, "w", encoding="utf-8") as f:
            f.write(content)
        _log(progress, f"   第 {idx + 1}/{len(chunks)} 块（{span}）完成")
        notes.append(content)
    _log(progress, "   合并分段笔记…")
    merged_input = "\n\n---\n\n".join(
        f"## 第 {i + 1} 段笔记\n\n{n}" for i, n in enumerate(notes))
    final, usage = deepseek_chat(api_key, MERGE_SYSTEM, merged_input,
                                 max_tokens=8192)
    total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
    total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
    return final


if __name__ == "__main__":
    import sys
    u = sys.argv[1] if len(sys.argv) > 1 else ""
    if not u:
        print("用法: python3 pipeline.py <回放URL>")
        sys.exit(1)
    r = run_pipeline(u)
    print(json.dumps({k: str(v) for k, v in r.items()}, ensure_ascii=False,
                     indent=1))
