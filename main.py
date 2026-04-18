import sublime
import sublime_plugin
import re
import os
from html import escape

class MarkdownCodeExporter(sublime_plugin.ViewEventListener):
    @classmethod
    def is_applicable(cls, settings):
        return settings.get('syntax', '').lower().find('markdown') != -1

    def __init__(self, view):
        super().__init__(view)
        self.phantom_set = sublime.PhantomSet(view)

        self.timeout_scheduled = False
        self.needs_update = False
        
        # 用于记录当前处于激活状态（鼠标悬停或光标所在）的代码块索引
        self.active_blocks = set()
        # 用于记录刚刚被点击了 "Copy" 的代码块索引，以提供视觉反馈
        self.copied_blocks = set()

        self.update_active_blocks(force_update=True)

    def on_modified(self):
        # 不处理大于 1MB 的文件，防止卡顿
        if self.view.size() > 2**20:
            return

        # 防抖动处理：限制更新频率
        if self.timeout_scheduled:
            self.needs_update = True
        else:
            self.timeout_scheduled = True
            sublime.set_timeout(lambda: self.handle_timeout(), 100)
            self.update_active_blocks(force_update=True)

    def on_load(self):
        self.update_active_blocks(force_update=True)

    def handle_timeout(self):
        self.timeout_scheduled = False
        if self.needs_update:
            self.needs_update = False
            self.update_active_blocks(force_update=True)

    # 监听鼠标悬停事件
    def on_hover(self, point, hover_zone):
        if hover_zone != sublime.HOVER_TEXT:
            return
        self.update_active_blocks(hover_point=point)

    # 监听光标位置改变事件
    def on_selection_modified(self):
        self.update_active_blocks()

    def update_active_blocks(self, hover_point=None, force_update=False):
        code_blocks = self.find_code_blocks()
        new_active = set()

        # 1. 检查光标是否在代码块内
        for sel in self.view.sel():
            for i, block in enumerate(code_blocks):
                if block['region'].contains(sel):
                    new_active.add(i)

        # 2. 检查鼠标悬停点是否在代码块内
        if hover_point is not None:
            for i, block in enumerate(code_blocks):
                if block['region'].contains(hover_point):
                    new_active.add(i)

        # 如果激活的代码块发生变化，或者强制要求更新（如文本被修改），则重绘 Phantom
        if force_update or new_active != self.active_blocks:
            self.active_blocks = new_active
            self.update_phantoms(code_blocks)

    def update_phantoms(self, code_blocks):
        phantoms =[]

        for i, block in enumerate(code_blocks):
            # 如果当前代码块未被激活（无悬停、无光标），则跳过，不渲染按钮
            if i not in self.active_blocks:
                continue

            region = block['region']
            insertion_point = block['insertion_point']

            # 检查当前代码块是否处于 "已复制" 状态
            is_copied = i in self.copied_blocks

            if is_copied:
                copy_text = "Copied!"
                # 复制成功后的样式：背景加深，文字反色（使用背景色作为文字色）
                copy_style = "color: var(--background); background-color: color(var(--foreground) alpha(0.7)); border-color: color(var(--foreground) alpha(0.7));"
            else:
                copy_text = "Copy"
                # 默认样式
                copy_style = "color: color(var(--foreground) alpha(0.8)); background-color: color(var(--foreground) alpha(0.04)); border-color: color(var(--foreground) alpha(0.1));"

            # 创建 Phantom 的 HTML 内容
            # 注意：使用 .format() 时，CSS 中的大括号需要双写 {{ 和 }} 进行转义
            content = '''
                <body id="markdown-code-exporter">
                    <style>
                        html, body {{
                            margin: 0;
                            padding: 0;
                        }}
                        .actions {{
                            margin-left: 20px;
                            font-family: system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                            font-size: 12px;
                            line-height: 1;
                        }}
                        a {{
                            padding: 2px 12px;
                            margin-right: 8px;
                            border-radius: 4px;
                            text-decoration: none;
                            font-weight: bold;
                            border: 1px solid;
                        }}
                        a.copy-btn {{
                            {copy_style}
                        }}
                        a.tab-btn {{
                            color: color(var(--foreground) alpha(0.8));
                            background-color: color(var(--foreground) alpha(0.04));
                            border-color: color(var(--foreground) alpha(0.1));
                        }}
                    </style>
                    <div class="actions">
                        <a href="copy" class="copy-btn">{copy_text}</a>
                        <a href="new_tab" class="tab-btn">Open in Tab</a>
                    </div>
                </body>
            '''.format(copy_style=copy_style, copy_text=copy_text)

            # 创建并添加 Phantom
            # 将当前代码块的索引 i 传递给 handle_phantom_click
            phantom = sublime.Phantom(
                sublime.Region(insertion_point, insertion_point),
                content,
                sublime.LAYOUT_INLINE,
                on_navigate=lambda href, r=region, idx=i: self.handle_phantom_click(href, r, idx)
            )
            phantoms.append(phantom)

        self.phantom_set.update(phantoms)

    def find_code_blocks(self):
        code_blocks =[]
        view = self.view

        # 查找所有 Markdown 代码块
        content = view.substr(sublime.Region(0, view.size()))
        pattern = r'^( *```+)[\w\-]*\n[\s\S]*?\n\1$'
        matches = re.finditer(pattern, content, re.MULTILINE)

        for match in matches:
            start = match.start()
            end = match.end()
            
            # 找到第一行（即 ```language 这一行）的末尾，作为按钮的插入点
            first_line_end = content.find('\n', start)
            if first_line_end == -1 or first_line_end > end:
                first_line_end = end
                
            code_blocks.append({
                'region': sublime.Region(start, end),
                'insertion_point': first_line_end
            })

        return code_blocks

    def handle_phantom_click(self, href, region, idx):
        # 获取代码内容（排除首尾的 ``` 行）
        lines = self.view.substr(region).split('\n')
        code = '\n'.join(lines[1:-1]) + "\n"

        if href == "copy":
            # 复制到剪贴板
            sublime.set_clipboard(code)
            sublime.status_message("Code copied to clipboard")

            # 1. 将当前代码块标记为已复制，并立即刷新界面
            self.copied_blocks.add(idx)
            self.update_phantoms(self.find_code_blocks())

            # 2. 定义一个恢复函数，1.5秒后将按钮状态还原
            def revert_copy_state():
                if idx in self.copied_blocks:
                    self.copied_blocks.remove(idx)
                    # 确保视图仍然有效时才刷新
                    if self.view.is_valid():
                        self.update_phantoms(self.find_code_blocks())

            sublime.set_timeout(revert_copy_state, 1500)

        elif href == "new_tab":
            # 在新标签页中打开
            new_view = self.view.window().new_file()
            new_view.run_command('append', {'characters': code})

            # 尝试检测并设置语法高亮
            identifier = re.sub(r"^ *`+", "", lines[0]).strip().lower()

            id_syntax_map =[
                {
                    "identifier": ["md", "markdown", "mdown"],
                    "syntaxes":[
                        "Packages/MarkdownEditing/Markdown.sublime-syntax",
                        "Packages/MarkdownEditing/MultiMarkdown.tmLanguage",
                        "Packages/MarkdownEditing/Markdown (Standard).tmLanguage",
                        "Packages/Markdown/Markdown.sublime-syntax",
                        "Packages/Markdown/MultiMarkdown.sublime-syntax",
                    ],
                },
                {
                    "identifier":["js", "javascript"],
                    "syntaxes":[
                        "Packages/JavaScript/JavaScript.sublime-syntax",
                    ],
                },
                {
                    "identifier":["html"],
                    "syntaxes":[
                        "Packages/HTML/HTML.sublime-syntax",
                    ],
                },
                {
                    "identifier": ["java"],
                    "syntaxes":[
                        "Packages/Java/Java.sublime-syntax",
                    ],
                },
                {
                    "identifier": ["php"],
                    "syntaxes":[
                        "Packages/PHP/PHP.sublime-syntax",
                    ],
                },
                {
                    "identifier": ["py", "python"],
                    "syntaxes":[
                        "Packages/Python/Python.sublime-syntax",
                    ],
                },
                {
                    "identifier": ["rb", "ruby"],
                    "syntaxes":[
                        "Packages/Ruby/Ruby.sublime-syntax",
                    ],
                },
                {
                    "identifier":["sh", "shell"],
                    "syntaxes":[
                        "Packages/ShellScript/Shell-Unix-Generic.sublime-syntax",
                    ],
                },
                {
                    "identifier": ["sql"],
                    "syntaxes": [
                        "Packages/SQL/SQL.sublime-syntax",
                    ],
                },
            ]

            if not identifier:
                return

            syntax_files = sublime.find_resources('*.tmLanguage') + sublime.find_resources('*.sublime-syntax')

            for mapping in id_syntax_map:
                if not identifier in mapping["identifier"]:
                    continue

                for syntax in mapping["syntaxes"]:
                    if syntax in syntax_files:
                        new_view.assign_syntax(syntax)
                        return

            for syntax_file in syntax_files:
                if identifier in os.path.basename(syntax_file.lower()):
                    new_view.assign_syntax(syntax_file)
                    return
