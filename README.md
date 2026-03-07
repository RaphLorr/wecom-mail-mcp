# WeCom Mail MCP

一个基于 Python 的 MCP 服务端，通过企业微信官方邮件 API 发送普通邮件。

它做的事情很简单：

- 对 AI 暴露 `send_email`
- 通过企业微信官方接口发送邮件
- 从环境变量或 `.env` 读取 `CORPID` / `CORPSECRET`
- 兼容 `uv run` 和 `uvx --from ...`

## 功能

- 官方 WeCom 邮件 API 发信，不走 SMTP
- 自动获取并缓存 `access_token`
- 兼容 `text` / `html`，也兼容 `text/plain` / `text/html`
- 提供 `get_mailbox_info`，方便确认当前发件邮箱
- 支持 `stdio`、`sse`、`streamable-http` 三种传输方式

## 你需要先准备什么

在企业微信侧确认下面几件事：

1. 有可用的 `CORPID`
2. 有对应应用的 `CORPSECRET`
3. 应用具备“邮件”权限
4. 应用邮箱账号已经配置好
5. 调用邮件接口所用的应用 secret 已经在“可调用应用”范围内

官方文档：

- 获取 access_token: <https://developer.work.weixin.qq.com/document/path/91039>
- 发送普通邮件: <https://developer.work.weixin.qq.com/document/path/97445>
- 查询应用邮箱账号: <https://developer.work.weixin.qq.com/document/path/97991>

## 环境变量 / `.env`

服务端会自动加载项目根目录下的 `.env`。
优先级是：命令行覆盖 > `.env` > 系统环境变量。
也就是说，`.env` 里写了就以 `.env` 为准；`.env` 没写或留空时，再回退到系统环境变量。

必填：

- `WECOM_CORP_ID`
- `WECOM_CORP_SECRET`

兼容别名：

- `CORPID`
- `CORPSECRET`

可选：

- `WECOM_API_BASE`，默认 `https://qyapi.weixin.qq.com`
- `WECOM_REQUEST_TIMEOUT`，默认 `20`
- `WECOM_MCP_TRANSPORT`，默认 `stdio`
- `WECOM_MCP_HOST`，默认 `127.0.0.1`
- `WECOM_MCP_PORT`，默认 `8000`
- `WECOM_LOG_LEVEL`，默认 `INFO`

## 本地运行

### 1. 安装依赖

```bash
uv sync
```

### 2. 配置 `.env`

项目里已经提供了 `.env.example` 和一个本地 `.env` 模板。

PowerShell 以外，最省事的方式就是直接编辑根目录 `.env`：

```env
CORPID=你的企业ID
CORPSECRET=你的应用Secret
```

如果你不想用 `.env`，也可以继续用系统环境变量。

### 3. 或者设置环境变量

PowerShell:

```powershell
$env:CORPID="你的企业ID"
$env:CORPSECRET="你的应用Secret"
```

### 4. 校验配置

```bash
uv run wecom-mail-mcp --check-config
```

成功时会输出当前应用邮箱账号和别名邮箱列表。

### 5. 以 stdio 启动 MCP

```bash
uv run wecom-mail-mcp
```

## `uvx` 可以吗

可以。对于 Python 项目，`uvx` 的角色基本就类似 Node 生态里的 `npx`。

本项目已经提供了 console script，所以本地目录可以直接这样跑：

```bash
uvx --from . wecom-mail-mcp
```

如果以后你把它发到 PyPI，上线后就可以直接：

```bash
uvx wecom-mail-mcp
```

## Claude Desktop 配置示例

```json
{
  "mcpServers": {
    "wecom-mail": {
      "command": "uvx",
      "args": [
        "--from",
        "d:/Code_Save/Py/发邮件的mcp",
        "wecom-mail-mcp"
      ],
      "env": {
        "CORPID": "你的企业ID",
        "CORPSECRET": "你的应用Secret"
      }
    }
  }
}
```

如果你更喜欢 `uv run`，也可以：

```json
{
  "mcpServers": {
    "wecom-mail": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "d:/Code_Save/Py/发邮件的mcp",
        "wecom-mail-mcp"
      ],
      "env": {
        "CORPID": "你的企业ID",
        "CORPSECRET": "你的应用Secret"
      }
    }
  }
}
```

## MCP 工具

### `send_email`

参数：

- `to_email`：收件人邮箱
- `subject`：邮件主题
- `content`：邮件正文
- `content_type`：可选，支持 `text`、`html`、`text/plain`、`text/html`，默认 `text`

说明：

- 发件人不是由 AI 传入的，而是企业微信“应用邮箱账号”
- 服务端会把 `text/plain` 映射为官方接口的 `text`
- 服务端会把 `text/html` 映射为官方接口的 `html`
- 如果发送 HTML，请显式传 `content_type="html"`
- MCP 工具描述会直接告知客户端 HTML 邮件兼容限制，避免把网页模板直接当邮件模板发送

### HTML 邮件兼容建议

如果要发 HTML 邮件，按最保守的邮件写法来：

- 优先使用 `table`、`tbody`、`tr`、`td` 做布局
- 文本和基础内容只用 `p`、`br`、`span`、`strong`、`b`、`em`、`i`、`h1` 到 `h4`、`a`、`img`
- 样式尽量写成 inline style，不要依赖复杂选择器
- 图片使用公网 `https` 绝对地址

尽量避免：

- `script`、`iframe`、`form`、`video`、`audio`、`canvas`、`svg`
- 外链 CSS、Web Font
- `flex`、`grid`、`position`
- 相对路径、本地路径、网页式复杂模板

推荐骨架：

```html
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
  <tr>
    <td>
      <h2 style="margin:0 0 16px;">标题</h2>
      <p style="margin:0 0 12px;">正文</p>
      <a href="https://example.com">链接</a>
      <img
        src="https://example.com/demo.png"
        alt=""
        style="display:block;width:100%;height:auto;border:0;"
      >
    </td>
  </tr>
</table>
```

### `get_mailbox_info`

返回当前应用邮箱账号与别名邮箱列表，便于确认发件人身份。

## HTTP 模式

如果你要用 HTTP 传输：

```bash
uv run wecom-mail-mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

## 开发测试

```bash
python -m unittest discover -s tests
```
