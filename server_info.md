# 远程服务器信息

## 服务器配置

服务器连接信息已移至 `.env` 文件中，请参考 `.env.example` 进行配置。

## 服务端口

| 服务 | 端口 |
|------|------|
| AI 对话服务 | 8888 (可通过 SERVER_PORT 配置) |

## 连接示例

```bash
# SSH 连接（从 .env 读取地址）
ssh $SSH_USER@$SSH_HOST

# 查看服务状态
curl http://<服务器IP>:8888/api/status

# 查看可用模型
curl http://<服务器IP>:8888/api/models
```

## 运维工具

```bash
python ops.py status    # 检查服务器状态
python ops.py logs      # 查看服务器日志
python ops.py restart   # 重启远程服务
python ops.py deploy    # 完整部署
python ops.py config    # 查看远程配置
```
