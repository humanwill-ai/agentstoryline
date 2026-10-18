"""Bounded OpenAI-compatible Chat Completions transport; never executes tools."""
import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request


class ProviderError(ValueError):
    pass


def validate_endpoint(endpoint):
    if not isinstance(endpoint, str) or len(endpoint) > 2048:
        raise ValueError('invalid provider endpoint')
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname:
        raise ValueError('endpoint must have a host and no credentials, query or fragment')
    local = parsed.hostname.lower() == 'localhost'
    try:
        local = local or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        pass
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and local):
        raise ValueError('provider requires HTTPS or loopback HTTP')
    return endpoint.rstrip('/') + '/chat/completions'


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def generate(endpoint, model, api_key, request):
    url = validate_endpoint(endpoint)
    body = json.dumps({'model': model, 'max_completion_tokens': 8192, 'response_format': {'type': 'json_object'}, 'messages': [
        {'role': 'system', 'content': request['instructions']},
        {'role': 'user', 'content': json.dumps(request, ensure_ascii=False)}]}, ensure_ascii=False).encode('utf-8')
    if len(body) > 600000:
        raise ProviderError('provider request exceeds size limit')
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if api_key:
        headers['Authorization'] = 'Bearer ' + api_key
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(urllib.request.Request(url, body, headers), timeout=30) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ProviderError('provider response exceeds size limit')
        result = json.loads(raw)
        choice = result['choices'][0]
        message = choice['message']
        if choice.get('finish_reason') == 'length':
            raise ProviderError('Provider response was incomplete (output limit); reduce the batch or change model.')
        if choice.get('finish_reason') == 'content_filter' or message.get('refusal'):
            raise ProviderError('Provider declined the description request.')
        if choice.get('finish_reason') == 'tool_calls' or message.get('tool_calls'):
            raise ProviderError('Provider returned unsupported tool calls; no tools were executed.')
        content = message['content']
        if not isinstance(content, str):
            raise ValueError()
        return json.loads(content)
    except ProviderError:
        raise
    except urllib.error.HTTPError as exc:
        status = exc.code if type(exc.code) is int and 100 <= exc.code <= 599 else 'error'
        exc.close()
        raise ProviderError(f'Provider returned HTTP {status}; review gateway, credentials and model, then retry explicitly.') from None
    except (TimeoutError, socket.timeout):
        raise ProviderError('Provider request timed out; retry explicitly.') from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise ProviderError('Provider request timed out; retry explicitly.') from None
        raise ProviderError('Provider transport failed; review gateway connectivity and retry explicitly.') from None
    except (ValueError, TypeError, KeyError, IndexError):
        raise ProviderError('Provider returned invalid JSON or an unsupported response shape.') from None
    except Exception:
        # Never surface gateway bodies, request headers, URLs, or credentials.
        raise ProviderError('Provider transport failed; review gateway connectivity and retry explicitly.') from None
