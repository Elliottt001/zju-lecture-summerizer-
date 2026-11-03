# 智云课堂课程总结 · ZJU Lecture Summarizer

> Paste a ZJU 智云课堂 (classroom.zju.edu.cn) replay URL → the tool fetches the
> lecture transcript, summarizes it section by section with DeepSeek, and
> produces a timestamped study note (`知识总结.md`).
>
> 粘贴一个浙大智云课堂回放地址，自动抓字幕 → DeepSeek 分段总结 → 生成带时间戳的
> 《知识总结.md》。**纯 Python 标准库，零依赖，macOS / Windows 通用。**

![python](https://img.shields.io/badge/python-3.9+-blue) ![deps](https://img.shields.io/badge/依赖-零（纯标准库）-green) ![license](https://img.shields.io/badge/license-MIT-lightgrey)

## 功能 Features

- **抓字幕**：调用智云课堂公开字幕接口，无需登录即可拿到整节课的语音识别文本
- **单次请求总结**：整课字幕一次性交给 DeepSeek 生成完整讲义，前后一致、不并行；仅当超出模型上下文时自动退化为顺序分块+合并
- **课件参考**：可上传 pptx / docx / txt / md 课件，提取文字注入总结，校准术语、帮助抓住重点
- **下载交付**：完成后界面提供「下载总结文档」「下载全部资料（zip）」按钮，文件直接存到你自己的下载目录
- **免复制登录态**：一键从本机 Edge 读取已登录的 Cookie（macOS 用 Keychain+内置 AES，Windows 用 DPAPI，均为标准库实现）
- **本地网页界面**：双击启动，浏览器里填表、看实时日志、下载结果
- **隐私**：Cookie 与 API key 只存在本机 `config.json`（权限 600），**永不上传**；所有请求只发往智云课堂和 DeepSeek 官方接口

## 三种总结模式

| 模式 | 流程 | 需要 | 适用 |
|---|---|---|---|
| 只抓字幕 | 平台字幕接口 → 字幕全文 | 无 | 存档字幕、不耗额度 |
| 字幕 + AI 总结（默认） | 平台字幕 → DeepSeek 单次请求总结 | DeepSeek key | 绝大多数课 |
| 增强识别 + AI 总结 | 下载视频 → ffmpeg 抽音频 → **Whisper large-v3 重新识别**（带课程术语提示）→ 总结 | key + ffmpeg + `pip install faster-whisper` | 口音重、平台字幕术语识别不准的课 |

增强识别模式里的 ffmpeg 可在界面一键下载（静态单文件，约 20MB，存于 `bin/`，不入库）；
Whisper 识别时自动注入课程术语提示词（贪婪算法、Huffman、NP 完全、多项式规约……），
显著减少专业词汇的同音误识别。总结提示词已预设（速览表 / 分章详解 / 公式清单 /
考试信息 / 考点预测五段式），也可在界面「高级」里自行修改。

## 快速开始 Quick Start

**即下载即用**：克隆后双击一个文件即可，无需安装任何依赖（Python 3.9+ 自带环境就够）。

| 系统 | 操作 |
|---|---|
| macOS | 双击 `start.command`（首次若提示"无法打开"：右键 → 打开 → 仍要打开） |
| Windows | 双击 `start.bat`（没装 Python 会提示去官网下载，安装时勾选 Add to PATH） |
| Linux | `sh start.sh` |

启动后浏览器自动打开 http://127.0.0.1:8765 ，**先点「环境自检」**，
看 Python / 智云课堂连通性 / DeepSeek key / Edge / ffmpeg 五项的绿黄红灯，
再填表运行。端口被占用时会自动顺延到 8766–8770。

三个**不可避免的一次性摩擦**（操作系统行为，非代码问题）：

1. **DeepSeek key 需自备**（platform.deepseek.com 申请，几块钱用很久）；
   不填则进入"只抓字幕"模式。
2. **macOS 首次自动读 Cookie 会弹钥匙串授权框** → 点"允许"；
   双击未签名脚本的 Gatekeeper 警告 → 右键打开。
3. **未来高精 Whisper 模式**首次使用需联网下载 ffmpeg 与模型（约 3GB）。

手动方式（等价）：

```bash
python3 app.py          # 启动网页界面
python3 pipeline.py "<回放URL>"   # 纯命令行
```

在页面里：

1. 粘贴回放地址（形如 `https://classroom.zju.edu.cn/livingroom?course_id=...&sub_id=...`）；
2. 填 DeepSeek API key（[platform.deepseek.com](https://platform.deepseek.com) 申请；留空则只抓字幕不总结）；
3. 点「自动读取 Edge 登录态」或手动粘贴 Cookie（**均可不填**，字幕接口公开）；
4. 点「开始」，完成后点「下载总结文档」把讲义保存到自己电脑。

也可以不用界面，直接命令行：

```bash
python3 pipeline.py "<回放URL>"
```

## 输出 Output

每次运行在工具目录 `output/<课程名>-<上课日期 MM.DD>/` 暂存，例如
`output/物联网技术基础与应用开发-10.21/`。日期取回放对应的上课日期（北京时间），
重名时追加 `（2）`、`（3）` 等序号，避免覆盖已有资料。
课程详情不可用时使用课程编号和「日期未知」，并在日志中提示。
**完成后在界面点下载按钮把文件取回自己电脑**：

```
知识总结.md            ← 最终讲义（带时间戳，可单独下载）
raw/transcript.txt             逐段带时间戳字幕全文
raw/transcript_by_minute.txt   按分钟合并的字幕
raw/ppt.json                   （若 Cookie 有效）PPT 文字层
raw/video.mp4 / audio.mp3      （增强识别模式）视频与音频
```

## 工作原理 How It Works

```
livingroom URL ──解析──▶ sub_id
                          │
        公开字幕接口 GET ◀─┘  （无需登录）
                          │
              按分钟合并 → 按 ~11k 字分块
                          │
        DeepSeek 并发 3 路 × N 块（分段笔记）
                          │
              DeepSeek 合并 → 知识总结.md
```

- 字幕接口：`courseapi/v3/web-socket/search-trans-result?sub_id=...`（公开）
- 自动读 Cookie：直接读 Edge 本地数据库并解密——
  - macOS：`~/Library/Application Support/Microsoft Edge/*/Cookies`，
    Keychain 里的 `Microsoft Edge Safe Storage` 主密码 + PBKDF2-SHA1(1003) + AES-128-CBC
    （AES 为本项目内置实现，经 FIPS-197 与 NIST 向量验证）
  - Windows：`%LOCALAPPDATA%\Microsoft\Edge\User Data\*\Network\Cookies`，DPAPI 解密
- 总结提示词针对课堂场景定制：证明分步不跳步、公式上下分式、保留时间戳、单列考点与考试通知

## 常见问题 FAQ

- **需要登录吗？** 字幕总结不需要。Cookie 只用于可选的 PPT 文字层增强，抓不到会自动降级。
- **macOS 首次运行弹钥匙串框？** 点"允许"（建议"始终允许"）。若报权限不足：
  系统设置 → 隐私与安全性 → 完全磁盘访问权限 → 添加你的终端。
- **401 / 402 / 429？** 分别是 DeepSeek key 错误 / 余额不足 / 限流（脚本自动退避重试）。
- **直播中能跑吗？** 不建议，字幕不全；课后回放再跑。
- **macOS 官方 Python 报 SSL 证书错误？** 脚本会自动降级处理；根治可运行
  `/Applications/Python 3.x/Install Certificates.command`。

## 声明 Disclaimer

本项目仅供**个人学习**使用：抓取的是本人有权限访问的课程回放，频率极低（一节课一次请求级）。
请遵守浙大智云课堂的使用条款，**不要**用于批量抓取、传播课件或任何商业用途。
Cookie 是你的登录凭证，请勿将 `config.json` 提交到任何仓库或发给他人（`.gitignore` 已默认排除）。

## 许可 License

[MIT](LICENSE)
