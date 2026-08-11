# 问墨·code 用户登录（GitHub OAuth）接入指南

问墨·code 现已支持**多用户登录**：用户用 GitHub 账号登录后，每个人有独立的数据目录（对话历史/工作区/文件互不干扰）。

## 一、启用登录需要 3 步

### 1. 创建 GitHub OAuth App
1. 打开 **https://github.com/settings/developers** → 点 **New OAuth App**
2. 填写：
   - **Application name**: 问墨·code
   - **Homepage URL**: `http://localhost:8000`（部署后改成你的地址）
   - **Authorization callback URL**: `http://localhost:8000/api/auth/github/callback`
3. 创建后拿到 **Client ID** 和 **Client Secret**（Secret 只显示一次，注意保存）

### 2. 配置凭据（二选一）
**方式 A：环境变量**（推荐，key 不进代码）
```bash
set GITHUB_CLIENT_ID=你的ClientID
set GITHUB_CLIENT_SECRET=你的ClientSecret
set WENMO_BASE_URL=http://localhost:8000
```
**方式 B：写入 settings.json**
```json
{
  "github_client_id": "你的ClientID",
  "github_client_secret": "你的ClientSecret",
  "base_url": "http://localhost:8000"
}
```
> 当前 auth.py 只读环境变量；如需 settings.json 支持，可在 auth.py 的 `is_configured()` 中补充读取。

### 3. 重启问墨
重启后验证：访问 `http://localhost:8000/api/auth/status` 应返回 `"oauth_configured": true`

## 二、登录流程（用户视角）
1. 打开问墨 → 顶栏右侧显示 **"登录"** 按钮（GitHub 图标）
2. 点击 → 跳转 GitHub 授权页 → 用户授权
3. 授权后自动回跳 → 顶栏显示用户头像 + 用户名
4. 点用户名 → 登出

## 三、数据隔离
每个用户登录后，数据存到独立目录：
```
%APPDATA%/问墨/users/<github用户名>/
    ├── history/      # 该用户的对话历史
    ├── workspace/    # 该用户的工作区
    └── files/        # 该用户的文件
```
会话有效期 **7 天**（过期自动失效，需重新登录）。

## 四、API 一览
| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/auth/status` | GET | 登录状态（当前用户 / 是否已配置 OAuth）|
| `/api/auth/login` | GET | 返回 GitHub 授权跳转 URL |
| `/api/auth/github/callback` | GET | OAuth 回调（内部）|
| `/api/auth/logout` | POST | 登出（清会话）|
| `/api/auth/users` | GET | 已注册用户列表 |

前端请求带 `X-Wenmo-Token` 请求头（存于 localStorage 的 `wenmo_token`）。

## 五、部署到服务器
- `WENMO_BASE_URL` 改为你的公网地址（如 `https://wenmo.example.com`）
- GitHub App 的 Homepage/Callback URL 同步改
- 建议启用 HTTPS（OAuth token 传输安全）
