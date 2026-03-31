# OpenClaw

## 官网

https://docs.openclaw.ai/zh-CN


## 概述

适用于任何操作系统的 AI 智能体 Gateway 网关，支持 WhatsApp、Telegram、Discord、iMessage 等。

### 使用

## 安装

```npm install -g openclaw@latest```

## 仪表盘

```openclaw gateway --port 18789```

### 设置密码

```
openclaw config set gateway.auth.mode "password"
openclaw config set gateway.auth.password "你的自定义密码"
```

### 公网访问

```openclaw config set gateway.bind "lan"```

增加配置

```"dangerouslyDisableDeviceAuth": true```

### 跨域

```openclaw config set gateway.controlUi.allowedOrigins '["<你的访问地址>"]'```

## 创建Agent

```openclaw agents add xxx```