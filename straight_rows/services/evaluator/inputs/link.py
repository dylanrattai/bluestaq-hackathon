from typing import Literal
from main import EvaluationResult

LinkType = Literal["link_down", "link_ok"]

def get_evaluation(all_link_history: dict[str, list[LinkType]]) -> EvaluationResult:
    """
    Evaluates the link history and returns an EvaluationResult.

    The evaluation is based on the link history:
    - If all links are "link_ok", all evaluation categories are set to 0.
    - If any link is "link_down", the equipment_fault category is set to 1, others remain 0.

    Args:
        all_link_history (dict[str, list[LinkType]]): A dictionary mapping link names to their history of statuses.

    Returns:
        EvaluationResult: The evaluation result based on the link history.

    Raises:
        ValueError: If the link_history is empty.
    """

    if not all_link_history:
        raise ValueError("all_link_history cannot be empty")

    link_history = [status for history in all_link_history.values() for status in history]
    if all(link == "link_ok" for link in link_history):
        return EvaluationResult(
            space_weather=0,
            jamming=0,
            spoofing=0,
            equipment_fault=0,
            multi_path=0
        )
    else:
        return EvaluationResult(
            space_weather=0,
            jamming=0,
            spoofing=0,
            equipment_fault=1,
            multi_path=0
        )