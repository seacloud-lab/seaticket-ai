from flask import g

class ToolSharedVars:
    @staticmethod
    def init():
        g.tools_shared = {}

    @staticmethod
    def get(key, default=None):
        return g.tools_shared.get(key, default)
    
    @staticmethod
    def set(key, value):
        g.tools_shared[key] = value

    @staticmethod
    def update(map):
        g.tools_shared.update(map)
