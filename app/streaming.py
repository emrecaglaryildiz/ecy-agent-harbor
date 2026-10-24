"""OpenAI-compatible SSE decoder; private reasoning never enters visible deltas."""
import json


def read_completion(response, agent):
    if 'text/event-stream' not in response.headers.get('Content-Type', ''):
        return json.loads(response.read())
    message = {'role': 'assistant', 'content': ''}
    calls, usage, finish, event = {}, {}, None, []
    def consume(lines):
        nonlocal finish, usage
        data = '\n'.join(lines)
        if not data or data == '[DONE]':
            return
        item = json.loads(data)
        if item.get('error'):
            raise ValueError('Sağlayıcı akış hatası')
        if item.get('usage'):
            usage = item['usage']
        for choice in item.get('choices', []):
            if choice.get('index', 0) != 0:
                continue
            delta = choice.get('delta', {})
            for key in ('content', 'reasoning_content'):
                if isinstance(delta.get(key), str):
                    message[key] = message.get(key, '') + delta[key]
                    if key == 'content' and agent.get('_on_delta'):
                        agent['_on_delta'](delta[key])
            if delta.get('reasoning_details'):
                message.setdefault('reasoning_details', []).extend(delta['reasoning_details'])
            for part in delta.get('tool_calls', []):
                call = calls.setdefault(part['index'], {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                if part.get('id'):
                    call['id'] = part['id']
                for key in ('name', 'arguments'):
                    call['function'][key] += part.get('function', {}).get(key, '')
            if choice.get('finish_reason'):
                finish = choice['finish_reason']
    for raw in response:
        if agent.get('_cancelled') and agent['_cancelled']():
            raise RuntimeError('Görev iptal edildi')
        line = raw.decode('utf-8').rstrip('\r\n')
        if not line:
            consume(event)
            event = []
        elif line.startswith('data:'):
            event.append(line[5:].lstrip(' '))
    consume(event)
    if calls:
        message['tool_calls'] = [calls[key] for key in sorted(calls)]
    # An interrupted stream must never be labelled completed, even after visible text.
    return {'choices': [{'message': message, 'finish_reason': finish or 'stream_interrupted'}], 'usage': usage}
