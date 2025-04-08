import os
from typing import Optional

from cozeloop import new_client
from cozeloop.integration.langchain.trace_callback import LoopTracer
from langchain.callbacks.base import BaseCallbackHandler

from browser_use.utils import singleton


@singleton
class CozeLoopClient:
	"""
	Service for tracing the LLM.
	"""

	def __init__(self, config: Optional[dict] = None) -> None:
		workspace_id = os.getenv('COZELOOP_WORKSPACE_ID')
		self.client = None
		self.config = config
		if workspace_id:
			self.client = new_client()

	def get_langchain_callback(self):
		if self.client:
			return LoopTracer.get_callback_handler(self.client)
		return BaseCallbackHandler()
