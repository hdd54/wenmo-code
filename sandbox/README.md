# 问墨任务沙箱

构建一次本地 OCI 镜像：

```powershell
docker build -t wenmo-agent:locked sandbox
```

终端任务默认以 `required` 模式运行：只挂载当前任务的 Git worktree，根文件系统只读、
网络关闭、删除全部 Linux capabilities，并限制进程数、内存和 CPU。Docker/Podman 或镜像
不可用时命令会失败关闭，不会悄悄降级为宿主机执行。开发者可在权限设置 API 中显式将
模式改为 `prefer` 或 `off`，后两者应只用于可信的本地任务。
