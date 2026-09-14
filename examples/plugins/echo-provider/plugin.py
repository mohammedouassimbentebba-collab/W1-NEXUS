from w1cip.sdk import PluginBase


class EchoProviderPlugin(PluginBase):
    def invoke_provider(self, request):
        task = request.get("task", {})
        return {
            "output_type": task.get("expected_output_type", "contribution"),
            "payload": {"echo": request.get("context", {})},
            "context_fields_used": list(request.get("context", {}).keys()),
            "metadata": {"plugin": "org.w1.examples.echo-provider"},
        }
