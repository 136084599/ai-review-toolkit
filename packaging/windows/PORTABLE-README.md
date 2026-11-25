# 清审 Windows x64 懒人包

完整解压后双击 **Start.cmd**。浏览器会打开 http://127.0.0.1:8765/。

包内带 Python 3.12、FFmpeg、RapidOCR 及 Whisper small 模型。可以直接使用本地规则、视频技术检查、自动转写及 OCR。打开「AI 审稿」或「AI 审视频」，添加自己的文件后开始检查。模型复核需要在「模型设置」填入自己的 DeepSeek Key。只使用本地检查时无需配置 API 密钥。

包内的 `data/models/small/` 是公开模型文件。审核记录、上传的视频、配置和密钥在本机使用时生成。停止服务请在启动窗口按 Ctrl+C。

[使用手册](README.md) · [Whisper 模型来源及许可证](https://huggingface.co/Systran/faster-whisper-small)
