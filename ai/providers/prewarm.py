"""One bounded synthetic request to the configured self-hosted model server."""

import json
import requests

from ai.providers.cancellable import CancellationScope, run_cancellable


def warm_local(provider, protocol, cancel_event):
    """Use the existing server/model; no user data, history or cloud fallback."""
    scope = CancellationScope()
    session = requests.Session()
    close = scope.add(session.close)
    timeout = (provider.get_cold_start_timeout_seconds() if protocol == 'ollama'
               else provider.get_timeout_seconds())

    def work():
        if cancel_event.is_set():
            raise InterruptedError('Warm-up cancelled.')
        model, error = provider.get_model(session)
        if error:
            raise RuntimeError('Configured local model unavailable.')
        if cancel_event.is_set():
            raise InterruptedError('Warm-up cancelled.')
        messages = [{'role': 'user', 'content': 'Hello'}]
        if protocol == 'ollama':
            path = '/api/chat'
            payload = {'model': model, 'messages': messages, 'stream': False,
                       'options': {'num_predict': 1}}
        else:
            path = '/v1/chat/completions'
            payload = {'model': model, 'messages': messages, 'stream': False, 'max_tokens': 1}
        with session.post(provider.get_base_url() + path, json=payload, timeout=timeout, stream=True) as response:
            close_response = scope.add(response.close)
            try:
                response.raise_for_status()
                # Consume a bounded body so ready means the request actually finished.
                body = bytearray()
                for chunk in response.iter_content(chunk_size=4096):
                    body.extend(chunk)
                    if len(body) > 65536 or cancel_event.is_set():
                        raise RuntimeError('Warm-up response invalid or cancelled.')
                payload = json.loads(body)
                if not isinstance(payload, dict) or payload.get('error') or (
                    protocol == 'ollama' and payload.get('done') is not True
                ) or (protocol != 'ollama' and not payload.get('choices')):
                    raise RuntimeError('Warm-up response invalid.')
            finally:
                close_response()

    try:
        run_cancellable(work, cancel_event, timeout + 10, 'Model warm-up timed out.', on_abandon=scope.cancel)
    finally:
        close()
