# AtlasWatch Transcript Sidecar

低资源字幕服务，只读取 YouTube 已有公开字幕或自动字幕，不下载视频、音频，也不执行语音识别。

## 本地运行

```bash
export TRANSCRIPT_SIDECAR_TOKEN="$(openssl rand -hex 32)"
docker build -t atlaswatch-transcript .
docker run --rm -p 8080:8080 \
  -e TRANSCRIPT_SIDECAR_TOKEN="$TRANSCRIPT_SIDECAR_TOKEN" \
  atlaswatch-transcript
```

健康检查：`GET /health`。字幕接口：

```bash
curl "http://127.0.0.1:8080/transcript?videoId=VIDEO_ID&languages=zh-CN,zh,en" \
  -H "Authorization: Bearer $TRANSCRIPT_SIDECAR_TOKEN"
```

## 生产约束

- 建议最低 0.5–1 vCPU、512 MB–1 GB 内存、单 Worker。
- 必须使用 HTTPS，并将 `TRANSCRIPT_SIDECAR_TOKEN` 作为密钥保存。
- 主站配置同一个 Token 和 `TRANSCRIPT_SIDECAR_URL`。
- 不要配置 YouTube Cookie；遇到受限、无字幕或地区限制的视频时明确返回不可用。
- 网络出口比 CPU 更重要；控制调用频率，避免把此服务用于全网批量抓取。
