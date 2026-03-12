class ChatCallBacker:
    def __init__(self):
        self.execution_detail = {}
    
    def __call__(self, func, *args, **kwargs):
        if func == 'update_execution_detail':
            data: dict = args[0]
            for key, value in data.items():
                self.execution_detail[f'{len(self.execution_detail) + 1}. {key}'] = value
