# XHS Voice Comment Tool Skill

一个面向 AI 编程代理的通用 tool skill，用来把小红书语音评论分享链接、复制分享文案、二维码分享图，或者直接的 `sns-video-v2.xhscdn.com` 音频链接，导出为本地 `MP3` 和 `WAV` 文件。

它不是 Codex 专属能力，而是一个可移植的 `SKILL.md` + 脚本包：支持目录式 skills 的代理可以直接挂载使用，例如 Codex、Claude Code/Cloud Code、Hermes 或其他兼容实现；不支持 skill 目录的环境也可以直接调用 `scripts/xhs-voice`。

它的目标是让使用者尽量少配置：普通链接和直链只依赖 Python；二维码识别、精准评论定位、音频转码等能力会在需要时自动准备到 tool skill 本地的 `.venv` 中。

## 能做什么

- 从小红书分享图中识别二维码链接。
- 解析 `xhslink.com` 短链和小红书 H5 落地页。
- 读取评论里的 `audioInfo.playInfo.url` 音频地址。
- 对带 `anchorCommentId` 的分享链接，使用小红书 H5 签名评论分页定位被分享的那一条语音评论。
- 默认同时保存 `.mp3` 和 `.wav`，并删除内部临时 MP4。
- 默认用语音评论的 ASR 文本作为文件名；如果 ASR 文本不可用，再回退到评论文本或元数据命名。

## 获取小红书语音评论分享图

在手机端先把语音评论变成一张可上传的分享图：

1. 在小红书评论区找到想保存的语音评论，长按这条语音评论。
2. 在弹出的分享菜单里点击“分享到微信”。
3. 在微信对话框里打开这张分享卡片图片，并保存图片至本地相册。
4. 把保存下来的图片上传给你的代理工具，或把图片路径传给脚本。

这张图片里通常包含小红书短链二维码。tool skill 会先识别二维码，再解析分享链接，定位对应的语音评论并导出音频文件。

## 安装到代理工具

```bash
git clone https://github.com/<your-github-name>/xhs-voice-comment-skill.git
```

把 `skills/xhs-voice-comment` 复制到对应代理工具的 skills 目录即可。常见安装方式：

```bash
# Codex
mkdir -p ~/.codex/skills
cp -R xhs-voice-comment-skill/skills/xhs-voice-comment ~/.codex/skills/

# Claude Code / Cloud Code
mkdir -p ~/.claude/skills
cp -R xhs-voice-comment-skill/skills/xhs-voice-comment ~/.claude/skills/
```

如果 Hermes 或其他代理使用不同的 skills 目录，把同一个 `skills/xhs-voice-comment` 文件夹放到它配置的目录中即可。核心约定是：代理能读取 `SKILL.md`，并能运行 `scripts/xhs-voice`。

安装后，向代理工具提供小红书语音评论分享图、分享链接或直链时，这个 tool skill 就可以触发或被调用。

## 在代理里使用

安装完成后，可以直接把图片拖进代理对话框，或者在消息里提供图片路径：

```text
帮我把这张小红书语音评论分享图转成 MP3 和 WAV。
```

也可以提供复制出来的小红书分享链接：

```text
帮我下载这个小红书语音评论，并保存成 MP3 和 WAV：https://xhslink.com/...
```

默认输出会保存在 `xhs_audio_exports/`，并且只保留两个最终音频文件：一个 `.mp3` 和一个 `.wav`。文件名默认来自语音评论的 ASR 文本。

如果分享链接带有 `anchorCommentId`，tool skill 会优先精准定位这条被分享的语音评论。找不到目标评论时会停止并提示原因，不会随便下载页面上的其他候选语音。

## 直接运行脚本

在仓库根目录运行：

```bash
skills/xhs-voice-comment/scripts/xhs-voice "<小红书分享链接或图片路径>" -o xhs_audio_exports
```

默认会同时生成 MP3 和 WAV，文件名默认来自语音评论的 ASR 文本。

只导出单一格式：

```bash
skills/xhs-voice-comment/scripts/xhs-voice "<小红书分享链接或图片路径>" -f mp3 -o xhs_audio_exports
skills/xhs-voice-comment/scripts/xhs-voice "<小红书分享链接或图片路径>" -f wav -o xhs_audio_exports
```

写出元数据清单：

```bash
skills/xhs-voice-comment/scripts/xhs-voice "<小红书分享链接或图片路径>" --write-manifest -o xhs_audio_exports
```

## 可选能力

脚本会按需自动准备依赖：

- `qr`：二维码图片识别，优先使用系统 `cv2` 或 `zbarimg`，缺失时安装 `opencv-python-headless`。
- `precision`：带 `anchorCommentId` 的精确评论定位，缺失时安装 Playwright 和 Chromium。
- `convert`：MP3/WAV 转码，优先使用系统 `afconvert` 或 `ffmpeg`，缺失时安装 `imageio-ffmpeg`。
- `tls`：本地 Python 证书链异常时安装 `certifi`。

也可以手动预装全部能力：

```bash
python3 skills/xhs-voice-comment/scripts/bootstrap_local.py --all
```

如需禁用自动安装：

```bash
XHS_VOICE_NO_AUTO_SETUP=1 skills/xhs-voice-comment/scripts/xhs-voice "<输入>"
```

## 边界说明

- 这个项目只处理用户主动提供的分享链接、分享图片或直接音频 URL。
- 小红书页面结构和签名逻辑可能变化，若平台接口调整，精准定位能力可能需要更新。
- 如果目标评论在公开 H5 数据和免登录分页中不可见，脚本会停止并提示，不会随便下载错误候选。
- Cookie 不是默认要求；只有用户明确提供或需要处理不可公开访问的内容时才使用。
- 本项目与小红书无官方关联。请只处理你有权访问和保存的内容，并遵守平台规则与相关法律。

## 仓库结构

```text
.
├── README.md
├── LICENSE
└── skills/
    └── xhs-voice-comment/
        ├── SKILL.md
        ├── agents/
        │   └── openai.yaml
        └── scripts/
            ├── bootstrap_local.py
            ├── xhs-voice
            └── xhs_voice_comment.py
```

## GitHub 仓库简介建议

Tool skill for exporting Xiaohongshu voice comments from share links, QR images, or direct audio URLs to local MP3 and WAV files.
