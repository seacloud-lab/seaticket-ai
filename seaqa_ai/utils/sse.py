import json

class SSE:
    @staticmethod
    def data(obj):
        return f'data: {json.dumps(obj)}\n\n'

    @staticmethod
    def status(status, detail=None):
        if status == 'Calling tools' and detail:
            tool_call_names = ''
            for tool_call in detail:
                if isinstance(tool_call, dict):
                    tool_call_names += tool_call['function']['name'] + ', '
                else:
                    tool_call_names = tool_call + ', '
            if tool_call_names:
                detail = tool_call_names[:-2]
            else:
                detail = None
        return SSE.data({
            'status': {
                'type': status,
                'detail': detail
            }
        })
    
    @staticmethod
    def think(string):
        return SSE.data({
            'think': string
        })
    
    @staticmethod
    def search_found(number):
        return SSE.data({
            "search_found": number
        })
    
    @staticmethod
    def answer(string):
        if string:
            return SSE.data({
                'answer': string
            })
    
    @staticmethod
    def results(answer, sources, thought_process):
        return SSE.data({
            'results': {
                'answer': answer,
                'sources': sources,
                'thought_process': thought_process
            }
        })
    
    @staticmethod
    def done():
        return 'data: [DONE]\n\n'
    
    @staticmethod
    def error(error_msg):
        return f'data: [ERROR: {error_msg}]'
