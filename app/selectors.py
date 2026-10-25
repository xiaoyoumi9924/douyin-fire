DOUYIN_CHAT_URL = "https://www.douyin.com/chat"

# Ordered alternatives keep page-specific changes isolated from the workflow.
LOGIN_MARKERS = (
    'text=私信',
    'input[placeholder*="搜索"]',
    '[role="textbox"][placeholder*="搜索"]',
)
LOGIN_REQUIRED_MARKERS = (
    'text=扫码登录',
    'text=验证码登录',
    'text=登录后',
)
RISK_MARKERS = (
    'text=安全验证',
    'text=完成验证',
    'text=验证身份',
)
SEARCH_INPUTS = (
    'input[placeholder*="搜索"]',
    'input[placeholder="搜索"]',  # 精确匹配备用 selector，兼容慢渲染时属性值变化
    '[role="textbox"][placeholder*="搜索"]',
    'input[aria-label*="搜索"]',
    '[role="textbox"][aria-label*="搜索"]',
)
# 私信会话列表的行。搜索建立在 IM 会话数据之上：列表还没同步出来时，搜索框
# 可以输入，但结果区只会显示「未搜索到相关内容」。开跑前等到至少一行会话可见，
# 能避免冷启动时前几位好友被误判为「搜索不到」。
CONVERSATION_ITEMS = (
    '[data-e2e="conversation-item"]',
    '[class*="conversationConversationItem"]',
    '[class*="conversation-item"]',
    '[class*="ConversationItem"]',
)
CHAT_PANEL_MARKERS = (
    '[class*="RightPanelHeader"]',
    '[class*="chatHeader"]',
    '[class*="ChatHeader"]',
    '[class*="messageContent"]',
    '[class*="chatContent"]',
    '[class*="MessagePanel"]',
)
MESSAGE_INPUTS = (
    '[data-contents="true"]',
    '.DraftEditor-editor [contenteditable="true"]',
    '.DraftEditor-root [contenteditable="true"]',
    '[contenteditable="true"][data-placeholder*="发送消息"]',
    '[contenteditable="true"][aria-label*="消息"]',
    '[contenteditable="true"]',
    'textarea[placeholder*="消息"]',
)
IMAGE_INPUTS = ('input[type="file"][accept*="image"]', 'input[type="file"]')
STICKER_BUTTONS = (
    'svg.messageMsgInputiconAction',
    'button[aria-label*="表情"]',
    '[role="button"][aria-label*="表情"]',
    '[title*="表情"]',
)
STICKER_PANELS = (
    '.componentsemojiemojiPanel',
    '[class*="emojiPanel"]',
    '[role="dialog"]',
    '[class*="sticker"]',
)
