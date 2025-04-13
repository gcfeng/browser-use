import asyncio
import logging
from inspect import signature
from typing import Any, Callable, Dict, Optional

from browser_use.agent.uitars.keyboard import transform_hotkey_input
from browser_use.agent.uitars.parser import get_element_by_bbox, get_screen_point, screen_point_to_bbox
from browser_use.agent.uitars.views import PredictionParsed
from browser_use.agent.views import ActionResult
from browser_use.browser.context import BrowserContext
from browser_use.dom.views import DOMElementNode
from browser_use.utils import time_execution_async, time_execution_sync

logger = logging.getLogger(__name__)


def get_screen_point_bbox(box: Optional[str], browser: BrowserContext) -> Dict[str, Any]:
	if not box:
		err_msg = '❌  Action:point - No box provided'
		logger.error(err_msg)
		raise ValueError(err_msg)
	viewport_info = browser.current_state.viewport_info
	if not viewport_info.width or not viewport_info.height:
		err_msg = '❌  Action:point - No browser viewport size provided'
		logger.error(err_msg)
		raise ValueError(err_msg)
	size = {'width': viewport_info.width, 'height': viewport_info.height}
	point = get_screen_point(box, size)
	bbox = screen_point_to_bbox({'x': point[0], 'y': point[1]}, size['width'], size['height'])

	return {'point': point, 'bbox': bbox}


def locate_element(start_box: Optional[str], browser: BrowserContext) -> Optional[DOMElementNode]:
	result = get_screen_point_bbox(start_box, browser)
	elem = get_element_by_bbox(result['bbox'], browser.current_state)
	return elem


class UITarsActionRegistry:
	def __init__(self):
		self.actions: Dict[str, Callable] = {}
		self.last_state: Dict[str, Any] = {}

	def action(self):
		"""Decorator for registering actions"""

		def decorator(func):
			action_name = func.__name__
			self.actions[action_name] = func
			return func

		return decorator

	@time_execution_async('--execute_action')
	async def execute_action(
		self,
		prediction: PredictionParsed,
		browser: Optional[BrowserContext] = None,
		sensitive_data: Optional[Dict[str, str]] = None,
	) -> Any:
		"""Execute a registered action"""
		action_name = prediction.action_type
		if action_name not in self.actions:
			raise ValueError(f'Action {action_name} not found')

		action = self.actions[action_name]
		try:
			sig = signature(action)
			parameters = list(sig.parameters.values())
			parameter_names = [param.name for param in parameters]

			# Check if the action requires browser
			if 'browser' in parameter_names and not browser:
				raise ValueError(f'Action {action_name} requires browser but none provided.')

			# Prepare arguments based on parameter type
			extra_args = {}
			if 'browser' in parameter_names:
				extra_args['browser'] = browser
			if 'last_state' in parameter_names:
				extra_args['last_state'] = self.last_state
			if action_name == 'type':
				if sensitive_data:
					extra_args['has_sensitive_data'] = True
				prediction.action_inputs
			return await action(prediction=prediction, **extra_args)

		except Exception as e:
			raise RuntimeError(f'Error executing action {action_name}: {str(e)}') from e


class UITarsController:
	def __init__(self):
		self.registry = UITarsActionRegistry()

		@self.registry.action()
		async def click(prediction: PredictionParsed, browser: BrowserContext):
			element_node = locate_element(prediction.action_inputs.start_box, browser)
			if not element_node:
				err_msg = '❌  Action:click - Cant match element'
				logger.error(err_msg)
				raise ValueError(err_msg)
			# if element has file uploader then dont click
			if await browser.is_file_uploader(element_node):
				msg = f'Index {element_node.highlight_index} - has an element which opens file upload dialog. To upload files please use a specific function to upload files '
				logger.info(msg)
				return ActionResult(extracted_content=msg, include_in_memory=True)

			session = await browser.get_session()
			initial_pages = len(session.context.pages)
			msg = None

			try:
				download_path = await browser._click_element_node(element_node)
				if download_path:
					msg = f'💾  Downloaded file to {download_path}'
				else:
					msg = f'🖱️  Clicked element with index {element_node.highlight_index}: {element_node.get_all_text_till_next_clickable_element(max_depth=2)}'

				logger.info(msg)
				logger.debug(f'Element xpath: {element_node.xpath}')
				if len(session.context.pages) > initial_pages:
					new_tab_msg = 'New tab opened - switching to it'
					msg += f' - {new_tab_msg}'
					logger.info(new_tab_msg)
					await browser.switch_to_tab(-1)
				return ActionResult(extracted_content=msg, include_in_memory=True)
			except Exception as e:
				logger.warning(f'Element not clickable with index {element_node.highlight_index} - most likely the page changed')
				return ActionResult(error=str(e))

		@self.registry.action()
		async def drag(prediction: PredictionParsed, browser: BrowserContext):
			drag_source = get_screen_point_bbox(prediction.action_inputs.start_box, browser)
			drag_target = get_screen_point_bbox(prediction.action_inputs.end_box, browser)
			source_point = drag_source['point']
			target_point = drag_target['point']

			page = await browser.get_current_page()
			try:
				try:
					# Try to move to source position
					await page.mouse.move(source_point[0], source_point[1])
					logger.debug(f'Moved to source position ({source_point[0]}, {source_point[1]})')
				except Exception as e:
					msg = f'Failed to move to source position: {str(e)}'
					logger.error(msg)
					return ActionResult(error=msg, include_in_memory=True)

				# Press mouse button down
				await page.mouse.down()

				# Move to target position with intermediate steps
				steps = 10
				delay_ms = 5
				for i in range(1, steps + 1):
					ratio = i / steps
					intermediate_x = int(source_point[0] + (target_point[0] - source_point[0]) * ratio)
					intermediate_y = int(source_point[1] + (target_point[1] - source_point[1]) * ratio)

					await page.mouse.move(intermediate_x, intermediate_y)

					if delay_ms > 0:
						await asyncio.sleep(delay_ms / 1000)

				# Move to final target position
				await page.mouse.move(target_point[0], target_point[1])

				# Move again to ensure dragover events are properly triggered
				await page.mouse.move(target_point[0], target_point[1])

				# Release mouse button
				await page.mouse.up()

				msg = f'🖱️ Dragged from ({source_point[0]}, {source_point[1]}) to ({target_point[0]}, {target_point[1]})'
				logger.info(msg)
				return ActionResult(extracted_content=msg, include_in_memory=True)
			except Exception as e:
				error_msg = f'Failed to perform drag and drop: {str(e)}'
				logger.error(error_msg)
				return ActionResult(error=error_msg, include_in_memory=True)

		@self.registry.action()
		async def type(
			prediction: PredictionParsed,
			browser: BrowserContext,
			has_sensitive_data: bool = False,
			last_state: Dict[str, Any] = {},
		):
			start_box = prediction.action_inputs.start_box
			if not start_box:
				start_box = last_state['start_box']
			element_node = locate_element(start_box, browser)
			if not element_node:
				err_msg = '❌  Action:type - Cant match element'
				logger.error(err_msg)
				raise ValueError(err_msg)
			content = prediction.action_inputs.content if prediction.action_inputs.content else ''
			await browser._input_text_element_node(element_node, content)
			if not has_sensitive_data:
				msg = f'⌨️  Input {content} into index {element_node.highlight_index}'
			else:
				msg = f'⌨️  Input sensitive data into index {element_node.highlight_index}'
			logger.info(msg)
			logger.debug(f'Element xpath: {element_node.xpath}')
			return ActionResult(extracted_content=msg, include_in_memory=True)

		@self.registry.action()
		async def scroll(prediction: PredictionParsed, browser: BrowserContext):
			point_bbox = get_screen_point_bbox(prediction.action_inputs.start_box, browser)
			point = point_bbox['point']
			direction = prediction.action_inputs.direction
			if not direction:
				msg = '❗️ Action:scroll - no direction'
				logger.warning(msg)
				return ActionResult(extracted_content=msg, include_in_memory=True)

			# Move to point first
			page = await browser.get_current_page()
			try:
				await page.mouse.move(point[0], point[1])
			except Exception as e:
				msg = f'Failed to move to source position: {str(e)}'
				logger.error(msg)
				return ActionResult(error=msg, include_in_memory=True)

			viewport_info = browser.current_state.viewport_info
			if direction == 'down':
				delta = viewport_info.height * 0.7
				await page.mouse.wheel(0, delta)
			elif direction == 'up':
				delta = viewport_info.height * 0.7
				await page.mouse.wheel(0, -delta)
			elif direction == 'right':
				delta = viewport_info.width * 0.7
				await page.mouse.wheel(0, delta)
			elif direction == 'left':
				delta = viewport_info.width * 0.7
				await page.mouse.wheel(0, -delta)

			msg = f'🔍  Scrolled {direction} the page'
			logger.info(msg)
			return ActionResult(
				extracted_content=msg,
				include_in_memory=True,
			)

		@self.registry.action()
		async def finished(prediction: PredictionParsed, browser: BrowserContext):
			return ActionResult(is_step_done=True, extracted_content=prediction.thought)

		@self.registry.action()
		async def hotkey(prediction: PredictionParsed, browser: BrowserContext):
			key = prediction.action_inputs.key
			if not key:
				err_msg = '❌  Action:hotkey - Key is None'
				logger.error(err_msg)
				raise ValueError(err_msg)
			keys = transform_hotkey_input(key)

			page = await browser.get_current_page()
			try:
				for key in keys:
					await page.keyboard.press(key)
			except Exception as e:
				logger.debug(f'Error sending key {keys}: {str(e)}')
				raise e
			msg = f'⌨️  Sent keys: {keys}'
			logger.info(msg)
			return ActionResult(extracted_content=msg, include_in_memory=True)

		@self.registry.action()
		async def wait(prediction: PredictionParsed, browser: BrowserContext):
			seconds = 3
			msg = f'🕒  Waiting for {seconds} seconds'
			logger.info(msg)
			await asyncio.sleep(seconds)
			return ActionResult(extracted_content=msg, include_in_memory=True)

	@time_execution_sync('--uitars-act')
	async def act(
		self,
		prediction: PredictionParsed,
		browser: Optional[BrowserContext] = None,
		sensitive_data: Optional[Dict[str, str]] = None,
	) -> ActionResult:
		"""Execute an action"""
		try:
			if prediction.action_inputs.start_box:
				self.registry.last_state['start_box'] = prediction.action_inputs.start_box
			if prediction.action_inputs.end_box:
				self.registry.last_state['end_box'] = prediction.action_inputs.end_box
			if prediction.action_inputs.start_coords:
				self.registry.last_state['start_coords'] = prediction.action_inputs.start_coords
			if prediction.action_inputs.end_coords:
				self.registry.last_state['end_coords'] = prediction.action_inputs.end_coords

			result = await self.registry.execute_action(prediction, browser=browser, sensitive_data=sensitive_data)
			if isinstance(result, str):
				return ActionResult(extracted_content=result)
			elif isinstance(result, ActionResult):
				return result
			elif result is None:
				return ActionResult()
			else:
				raise ValueError(f'Invalid action result type: {type(result)} of {result}')
		except Exception as e:
			raise e
