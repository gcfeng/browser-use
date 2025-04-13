# https://github.com/bytedance/UI-TARS-desktop/blob/main/packages/ui-tars/action-parser/src/actionParser.ts
import json
import re
from typing import Any, Dict, Optional, Tuple

from browser_use.agent.uitars.views import ActionInputs, PredictionParsed
from browser_use.browser.views import BrowserState
from browser_use.dom.views import DOMElementNode


def parse_action_vlm(
	text: str,
	factors: Tuple[float, float] = (1000, 1000),
	screen_context: Optional[Dict[str, float]] = None,
	scale_factor: Optional[float] = None,
) -> list[PredictionParsed]:
	reflection: Optional[str] = None
	thought: Optional[str] = None
	action_str = ''

	text = text.strip()
	if text.startswith('Thought:'):
		thought_match = re.search(r'Thought: ([\s\S]+?)(?=\s*Action:|$)', text)
		if thought_match:
			thought = thought_match.group(1).strip()
	elif text.startswith('Reflection:'):
		reflection_match = re.search(r'Reflection: ([\s\S]+?)Action_Summary: ([\s\S]+?)(?=\s*Action:|$)', text)
		if reflection_match:
			thought = reflection_match.group(2).strip()
			reflection = reflection_match.group(1).strip()
	elif text.startswith('Action_Summary:'):
		summary_match = re.search(r'Action_Summary: (.+?)(?=\s*Action:|$)', text)
		if summary_match:
			thought = summary_match.group(1).strip()

	if 'Action:' not in text:
		action_str = text
	else:
		action_parts = text.split('Action:')
		action_str = action_parts[-1]

	all_actions = action_str.split('\n\n')
	actions: list[PredictionParsed] = []

	for raw_str in all_actions:
		action_instance = parse_action(raw_str.replace('\n', r'\n').lstrip())
		action_type = ''
		action_inputs = ActionInputs()

		if action_instance:
			action_type = action_instance['function']
			params = action_instance['args']

			for param_name, param in params.items():
				if not param:
					continue
				trimmed_param = str(param).strip()
				setattr(action_inputs, param_name.strip(), trimmed_param)

				if 'start_box' in param_name or 'end_box' in param_name:
					ori_box = trimmed_param
					numbers = [n for n in re.sub(r'[()[\]]', '', ori_box).split(',') if n]

					float_numbers = [float(num) / factors[idx % 2] for idx, num in enumerate(numbers)]

					if len(float_numbers) == 2:
						float_numbers.extend([float_numbers[0], float_numbers[1]])

					setattr(action_inputs, param_name.strip(), json.dumps(float_numbers))

					if screen_context and screen_context.get('width') and screen_context.get('height'):
						box_key = 'start_coords' if 'start_box' in param_name else 'end_coords'
						x1, y1 = float_numbers[0], float_numbers[1]
						x2, y2 = (
							float_numbers[2] if len(float_numbers) > 2 else x1,
							float_numbers[3] if len(float_numbers) > 3 else y1,
						)
						width_factor, height_factor = factors

						if all(isinstance(n, (int, float)) for n in [x1, y1, x2, y2]):
							coords = [
								(round(((x1 + x2) / 2) * screen_context['width'] * width_factor) / width_factor)
								* (scale_factor or 1),
								(round(((y1 + y2) / 2) * screen_context['height'] * height_factor) / height_factor)
								* (scale_factor or 1),
							]
							setattr(action_inputs, box_key, coords)
						else:
							setattr(action_inputs, box_key, [])

		actions.append(
			PredictionParsed(reflection=reflection, thought=thought or '', action_type=action_type, action_inputs=action_inputs)
		)

	return actions


def parse_action(action_str: str) -> Optional[Dict[str, Any]]:
	try:
		function_pattern = r'^(\w+)\((.*)\)$'
		match = re.match(function_pattern, action_str.strip())

		if not match:
			raise ValueError('Not a function call')

		function_name, args_str = match.groups()
		kwargs = {}

		if args_str.strip():
			# arg_pairs = re.findall(r"(\w+='(?:[^']|'(?!\s*,\s*\w+=))*')", args_str)
			arg_pairs = parse_action_args_pair(args_str)

			for pair in arg_pairs:
				parts = pair.split('=', 1)
				if not parts[0]:
					continue

				key = parts[0].strip()
				value = parts[1].strip() if len(parts) > 1 else ''
				value = value.strip('\'"')  # Remove surrounding quotes

				if '<bbox>' in value:
					value = value.replace('<bbox>', '').replace('</bbox>', '').replace(' ', ',')
					value = f'({value})'

				kwargs[key] = value

		return {'function': function_name, 'args': kwargs}
	except Exception as e:
		print(f"Failed to parse action '{action_str}': {e}")
		return None


def parse_action_args_pair(args_str) -> list[str]:
	# "start_box='='\n(231,540)"
	args_str = re.sub(r'[\'"]=[\'"](\s|\\n)*', '', args_str)
	# "start_box=\\'=\\'\\n(231,540)"
	args_str = re.sub(r'\\[\'"]=\\[\'"](\s|\\n)*', '', args_str)
	pattern = r"(\w+)=(?:'([^']*)'|\"([^\"]*)\"|(\(.*\))|([^,\s]+))"
	matches = re.findall(pattern, args_str)
	result = []
	for match in matches:
		key = match[0]
		value = next(v for v in match[1:] if v)
		result.append(f'{key}={value}')
	return result


def convert_bbox_to_coordinates(text: str) -> str:
	"""
	Converts bounding box notation to coordinate points

	Args:
	    text: The text containing bbox tags to be converted

	Returns:
	    The text with bbox tags replaced by coordinate points
	"""
	# Match the four numbers after <bbox>
	pattern = r'<bbox>(\d+)\s+(\d+)\s+(\d+)\s+(\d+)</bbox>'

	def replace_match(match) -> str:
		# Convert strings to numbers and calculate center point
		x1 = int(match.group(1))
		y1 = int(match.group(2))
		x2 = int(match.group(3))
		y2 = int(match.group(4))

		# Calculate center point
		x = (x1 + x2) // 2  # Using // for floor division
		y = (y1 + y2) // 2

		# Return formatted coordinate string
		return f'({x},{y})'

	# Remove [EOS] and replace <bbox> coordinates
	cleaned_text = text.replace('[EOS]', '')
	return re.sub(pattern, replace_match, cleaned_text).strip()


def get_screen_point(start_box: str, size: dict) -> Tuple[float, float]:
	"""
	Convert JSON string coordinates to actual screen coordinates

	Args:
	    start_box (str): JSON string containing [x, y] coordinates (0-1 range)
	    size (dict): Dictionary with width and height of the screen/container

	Returns:
	    tuple[float, float]: Screen coordinates as (x, y) tuple
	"""
	coordinates = json.loads(start_box)
	x, y = coordinates[:2]
	return (x * size['width'], y * size['height'])


BBOX_SIZE = 10


def screen_point_to_bbox(point: dict, width: int, height: int) -> Tuple[int, int, int, int]:
	"""
	Convert a point to a bounding box coordinates.

	Args:
	    point (dict): A dictionary containing x and y coordinates
	    width (int): The maximum width boundary
	    height (int): The maximum height boundary

	Returns:
	    tuple: A tuple of 4 integers representing (x1, y1, x2, y2) coordinates of the bbox
	"""
	return (
		round(max(point['x'] - BBOX_SIZE / 2, 0)),
		round(max(point['y'] - BBOX_SIZE / 2, 0)),
		round(min(point['x'] + BBOX_SIZE / 2, width)),
		round(min(point['y'] + BBOX_SIZE / 2, height)),
	)


def get_element_by_bbox(bbox: Tuple[int, int, int, int], state: BrowserState) -> Optional[DOMElementNode]:
	"""Get closest interactive element by bbox"""
	all_matches: list[DOMElementNode] = []
	for el in state.selector_map.values():
		coord = el.viewport_coordinates
		if not coord:
			continue
		if coord.top_left.x <= bbox[0] <= coord.top_right.x and coord.top_left.y <= bbox[1] <= coord.bottom_left.y:
			all_matches.append(el)

	smallest_area = float('inf')
	element = None
	for el in all_matches:
		current_area = el.viewport_coordinates.width * el.viewport_coordinates.height  # type: ignore
		if current_area < smallest_area:
			element = el
			smallest_area = current_area

	if not element:
		return None

	# center_x = (bbox[0] + bbox[2]) // 2
	# center_y = (bbox[1] + bbox[3]) // 2

	# dis = math.sqrt(
	# 	abs(element.viewport_coordinates.center.x - center_x) ** 2 + abs(element.viewport_coordinates.center.y - center_y) ** 2  # type: ignore
	# )

	# return element if dis <= 30 else None
	return element


def get_summary(prediction: str) -> str:
	return re.sub(r'Reflection:[\s\S]*?(?=Action_Summary:|Action:|$)', '', prediction).strip()
