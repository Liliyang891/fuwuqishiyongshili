#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Web 工具 — 映射自 Claude Code: src/tools/WebFetchTool/ + WebSearchTool/

WebFetch: URL 内容获取+处理
WebSearch: Web 搜索
"""

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser

from ..tool_base import Tool
from ..permissions import PermissionResult

logger = logging.getLogger(__name__)

FETCH_TIMEOUT = 30
MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5MB
FETCH_CACHE_TTL = 15 * 60  # 15 分钟


class _HTMLToText(HTMLParser):
    """将 HTML 转换为纯文本"""

    def __init__(self):
        super().__init__()
        self.text = []
        self.skip_tags = {'script', 'style', 'head', 'nav', 'footer'}

    def handle_data(self, data):
        self.text.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in ('br', 'p', 'li', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self.text.append('\n')
        elif tag in ('td', 'th'):
            self.text.append(' | ')

    def get_text(self) -> str:
        text = ''.join(self.text)
        lines = [line.strip() for line in text.split('\n')]
        return '\n'.join(line for line in lines if line)


class WebFetch(Tool):
    """URL 内容获取工具

    获取网页内容并转换为 Markdown 格式。
    内置 15 分钟缓存。
    """

    name = 'WebFetch'
    description = (
        '获取网页 URL 内容并处理。自动将 HTML 转为纯文本。'
        '内置缓存: 相同 URL 15 分钟内不重复请求。'
    )
    is_concurrency_safe = True
    is_read_only = True
    min_role = 'guest'
    tool_category = 'web'

    _cache: dict[str, tuple[float, str]] = {}

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'url': {
                    'type': 'string',
                    'description': '要获取的完整 URL (HTTP 自动升级为 HTTPS)',
                },
                'prompt': {
                    'type': 'string',
                    'description': '从页面中提取特定信息的提示',
                },
            },
            'required': ['url'],
        }

    def prompt(self):
        return """## Web 内容获取 (WebFetch)
- 获取网页 URL 内容, 自动将 HTML 转为纯文本
- 内置 15 分钟结果缓存, 相同 URL 不重复请求
- 用于查阅最新文档、API 参考、技术文章等"""

    def validate_input(self, arguments, context=None):
        url = arguments.get('url', '')
        if not url:
            return False, 'url 不能为空'
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
            arguments['url'] = url
        return True, ''

    def check_permissions(self, arguments, user=None) -> PermissionResult:
        url = arguments.get('url', '')
        # 阻止访问内网地址
        try:
            from urllib.parse import urlparse
            hostname = urlparse(url).hostname or ''
            if hostname in ('localhost', '127.0.0.1', '::1', '0.0.0.0'):
                return PermissionResult.deny('禁止访问本地地址')
            if hostname.startswith('10.') or hostname.startswith('192.168.'):
                return PermissionResult.deny('禁止访问内网地址')
        except Exception:
            pass
        return PermissionResult.allow()

    def call(self, arguments, user=None, context=None) -> dict:
        url = arguments['url']
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url

        # 检查缓存
        now = time.time()
        cached = self._cache.get(url)
        if cached and now - cached[0] < FETCH_CACHE_TTL:
            return {
                'success': True,
                'result': cached[1],
                'from_cache': True,
            }

        try:
            req = urllib.request.Request(
                url,
                headers={
                    'User-Agent': 'Mozilla/5.0 (compatible; AIServer/1.0)',
                    'Accept': 'text/html,application/xhtml+xml,text/plain',
                }
            )
            resp = urllib.request.urlopen(req, timeout=FETCH_TIMEOUT)
            content_type = resp.headers.get('Content-Type', '')

            if 'html' in content_type:
                raw = resp.read(MAX_CONTENT_LENGTH).decode(
                    resp.headers.get_content_charset('utf-8'),
                    errors='replace'
                )
                parser = _HTMLToText()
                parser.feed(raw)
                text = parser.get_text()
            else:
                text = resp.read(MAX_CONTENT_LENGTH).decode('utf-8', errors='replace')

            if len(text) > 50000:
                text = text[:50000] + '\n... (内容已截断)'

            # 缓存
            self._cache[url] = (now, text)

            return {
                'success': True,
                'result': text,
                'from_cache': False,
                'content_length': len(text),
            }
        except urllib.error.HTTPError as e:
            return {
                'success': False,
                'error': f'HTTP {e.code}: {e.reason}',
                'error_type': 'http_error',
            }
        except urllib.error.URLError as e:
            return {
                'success': False,
                'error': f'URL 错误: {e.reason}',
                'error_type': 'url_error',
            }
        except Exception as e:
            return {
                'success': False,
                'error': f'获取失败: {e}',
                'error_type': 'fetch_error',
            }


class WebSearch(Tool):
    """Web 搜索工具

    使用 DuckDuckGo 进行 Web 搜索。
    注意: 受限于服务可用性, 可能不总是返回结果。
    """

    name = 'WebSearch'
    description = (
        'Web 搜索工具。使用搜索引擎获取最新信息, '
        '返回搜索结果及链接。用于查阅最新技术文档、'
        'API 变更、新闻事件等。'
    )
    is_concurrency_safe = True
    is_read_only = True
    min_role = 'guest'
    tool_category = 'web'

    def input_schema(self):
        return {
            'type': 'object',
            'properties': {
                'query': {
                    'type': 'string',
                    'description': '搜索查询',
                    'minLength': 2,
                },
                'allowed_domains': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': '仅显示来自指定域名的结果',
                },
                'blocked_domains': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': '排除来自指定域名的结果',
                },
            },
            'required': ['query'],
        }

    def prompt(self):
        return """## Web 搜索 (WebSearch)
- 搜索 Web 获取最新信息
- 支持域名过滤 (允许/阻止列表)
- 返回结果摘要和链接"""

    def validate_input(self, arguments, context=None):
        query = arguments.get('query', '')
        if not query or len(query) < 2:
            return False, 'query 至少需要 2 个字符'
        return True, ''

    def check_permissions(self, arguments, user=None) -> PermissionResult:
        return PermissionResult.allow()

    def call(self, arguments, user=None, context=None) -> dict:
        query = arguments['query']
        allowed = arguments.get('allowed_domains', [])
        blocked = arguments.get('blocked_domains', [])

        results = []
        try:
            # 使用 DuckDuckGo HTML 搜索
            import urllib.parse
            encoded_query = urllib.parse.quote(query)
            search_url = f'https://html.duckduckgo.com/html/?q={encoded_query}'

            req = urllib.request.Request(
                search_url,
                headers={
                    'User-Agent': 'Mozilla/5.0 (compatible; AIServer/1.0)',
                }
            )
            resp = urllib.request.urlopen(req, timeout=FETCH_TIMEOUT)
            html = resp.read().decode('utf-8', errors='replace')

            # 简单解析搜索结果
            link_pattern = re.compile(
                r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                re.DOTALL
            )
            snippet_pattern = re.compile(
                r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
                re.DOTALL
            )

            links = link_pattern.findall(html)
            snippets = snippet_pattern.findall(html)

            for i, (url, title) in enumerate(links[:10]):
                url = url.strip()
                title = re.sub(r'<[^>]+>', '', title).strip()
                try:
                    from urllib.parse import urlparse
                    hostname = urlparse(url).hostname or ''
                    if allowed and not any(d in hostname for d in allowed):
                        continue
                    if blocked and any(d in hostname for d in blocked):
                        continue
                except Exception:
                    pass

                snippet = ''
                if i < len(snippets):
                    snippet = re.sub(r'<[^>]+>', '', snippets[i]).strip()

                results.append({
                    'title': title,
                    'url': url,
                    'snippet': snippet,
                })

        except Exception as e:
            logger.warning('WebSearch failed: %s', e)
            return {
                'success': False,
                'error': f'搜索失败: {e}。请稍后重试。',
                'error_type': 'search_error',
            }

        if not results:
            return {
                'success': True,
                'result': f'搜索 "{query}" 未找到结果。',
                'results': [],
            }

        return {
            'success': True,
            'result': json.dumps(results, ensure_ascii=False, indent=2),
            'results': results,
            'query': query,
        }
