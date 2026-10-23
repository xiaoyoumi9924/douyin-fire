---
name: 抖音自动续火花 · 本地控制面板
description: macOS 风格的轻量控制台，以清楚的任务状态和直接的操作为主。
colors:
  canvas: "#F5F5F7"
  surface: "#FFFFFF"
  ink: "#1D1D1F"
  secondary: "#636366"
  faint: "#6E6E73"
  border: "#E5E5EA"
  accent: "#0066CC"
  danger: "#D70015"
  success: "#34C759"
  field: "#F5F5F7"
typography:
  base:
    fontFamily: '-apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", "Microsoft YaHei", sans-serif'
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.5
  heading:
    fontSize: 30px
    fontWeight: 700
    letterSpacing: -0.8px
  data:
    fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace'
    fontSize: 11px
    lineHeight: 1.65
rounded:
  control: 9px
  list-item: 12px
  surface: 15px
  navigation: 16px
---

# Design System: 本地控制面板

## Overview

这是一个用于日常管理抖音私信自动发送任务的本地桌面网页。界面优先展示今天的运行状态和主要动作，好友、消息、定时、登录凭证与运行记录按使用频率排列。视觉参考 macOS 的系统界面：中性浅灰画布、白色内容表面、系统字体和克制的圆角。

页面不模拟抖音聊天，也不使用邮票册、邮戳或虚构统计数据。磁盘上的配置和运行结果仍是真值，页面只提供它们的可视化编辑和状态查看。

## Colors

| Token | 值 | 用途 |
| --- | --- | --- |
| `canvas` | `#F5F5F7` | 页面背景 |
| `surface` | `#FFFFFF` | 导航、内容面板、好友条目 |
| `ink` | `#1D1D1F` | 标题与正文 |
| `secondary` | `#636366` | 说明文字和次要状态 |
| `faint` | `#6E6E73` | 日期、占位符 |
| `border` | `#E5E5EA` | 细分隔线与控件边框 |
| `accent` | `#0066CC` | 主操作、焦点与当前导航 |
| `danger` | `#D70015` | 清除凭证及错误状态 |
| `success` | `#34C759` | 登录有效等成功状态 |
| `field` | `#F5F5F7` | 搜索框和文本输入区 |

颜色不能单独表达运行结果；状态文字需要同时显示。

## Layout

- 桌面端使用固定宽度的左侧导航和独立内容区；内容区最大宽度 1320px，页面背景为浅灰，导航与内容面板为白色。
- 内容区直接显示当前页面标题，运行状态保留在左侧导航底部；不再显示重复的账号面包屑、本机状态和设置顶栏。
- 首页集中展示今日投递进度、最近运行、下次执行、立即发送、少量好友状态和最近一次结果。
- 好友、自动任务、消息、登录与记录分别使用独立页面；凭证和完整运行历史集中在登录与记录页面。
- 760px 以下导航移到内容上方并横向滚动；540px 以下首页与设置区改为单列，表格在容器内横向滚动。

## Components

| 组件 | 规则 |
| --- | --- |
| 导航 | 白色表面、细边框、柔和投影；桌面滚动时保持可见 |
| 内容面板 | 纯白背景、15px 圆角、轻边框和低对比投影 |
| 主按钮 | 系统蓝色实底，用于立即发送 |
| 次按钮 | 白色或浅灰底，用于检查、保存和辅助操作 |
| 危险按钮 | 红色描边，二次确认后清除登录凭证 |
| 好友表格 | 连续分组行、圆形编号标记、文字与颜色并用的发送状态；双击昵称可改名 |
| 输入框 | 浅灰底、圆角；焦点使用清楚的蓝色外框 |
| 运行历史 | 表格展示完成时间、运行方式、结果、好友数量与逐好友详情 |

## Interaction and Accessibility

- 所有操作可用键盘到达；焦点样式始终可见。
- 删除好友与清除凭证都要求二次确认。
- 发送期间禁用重复触发，并显示当前运行状态和处理进度。
- Dry Run 标记为检查结果，不计入真实发送进度。
- 支持 `prefers-reduced-motion`；动效仅用于短暂的状态反馈。
- 页面不加载外部字体、图标、CDN 或前端构建依赖。

## Do / Don't

**Do**
- 让用户先看到任务状态和下一步操作。
- 用留白、字重、细边框和轻投影组织层级。
- 把危险动作和发送错误保留为红色，其余操作使用中性或系统蓝色。
- 只显示登录凭证状态，不显示凭证内容。

**Don't**
- 不用装饰性仪表、虚构数据、邮票或邮戳来代替真实状态。
- 不使用渐变背景、重投影、卡片套卡片或额外高饱和色。
- 不把好友昵称或消息文本写入展示用的示例统计。
