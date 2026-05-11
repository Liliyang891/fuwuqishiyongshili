FROM python:3.11-slim

WORKDIR /app

# 安装 ripgrep (GrepTool 高性能搜索)
RUN apt-get update && apt-get install -y --no-install-recommends ripgrep && rm -rf /var/lib/apt/lists/*

# 安装依赖
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用文件
COPY web_server.py ./
COPY tools.py ./
COPY auth.py ./
COPY role_levels.py ./
COPY agent/ ./agent/
COPY static/ ./static/

# 创建工作数据目录
RUN mkdir -p /app/data/files

EXPOSE 8888

CMD ["python", "web_server.py"]
