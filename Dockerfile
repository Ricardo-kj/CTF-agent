# CTFY Agent Starter —— 最小可提交镜像
#
# 构建（注意 --platform：平台大概率是 linux/amd64）：
#   docker build --platform linux/amd64 -t ctfy-agent-starter:0.1 .
#
# 运行自测：
#   docker run --rm -p 8080:8080 -e LLM_API_KEY=sk-xxx ctfy-agent-starter:0.1

FROM python:3.12-slim

# 基础工具：排障用；CTF 工具链按需在下方面加，别把镜像撑太大
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates file binutils \
 && rm -rf /var/lib/apt/lists/*

# 需要 pwntools / z3 / requests 等再放开（会显著增大镜像）：
# RUN pip install --no-cache-dir pwntools z3-solver requests pycryptodome

WORKDIR /app
COPY agent.py /app/agent.py

RUN useradd -m -u 10001 agent \
 && mkdir -p /workspace \
 && chown -R agent:agent /app /workspace

USER agent
ENV PYTHONUNBUFFERED=1 \
    PORT=8080 \
    WORKDIR=/workspace \
    LLM_BASE_URL=https://api.deepseek.com/v1 \
    LLM_MODEL=deepseek-chat

# 注意：不要在这里 ENV LLM_API_KEY=...！密钥由平台第二步或运行时 -e 注入。

EXPOSE 8080

# 平台健康检查可能直接打这个端点；容器自身也带 HEALTHCHECK
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status == 200 else 1)"

ENTRYPOINT ["python", "/app/agent.py"]
