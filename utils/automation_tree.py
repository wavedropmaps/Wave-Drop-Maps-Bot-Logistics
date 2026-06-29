import logging
import asyncio
from typing import Optional, Dict, Any, Tuple
import torch
from ultralytics import YOLO

logger = logging.getLogger('ProofAutomationTree')

class Decision:
    """
    Represents the output of a node in the automation tree.
    """
    def __init__(self, action: str, next_node: str = None, message_key: str = None, roles_to_grant: list = None, confidence: float = 0.0, class_name: str = None, failed_node: str = None):
        self.action = action  # 'ROUTE', 'REJECT', 'GRANT', 'HITL'
        self.next_node = next_node
        self.message_key = message_key
        self.roles_to_grant = roles_to_grant or []
        self.confidence = confidence
        self.class_name = class_name
        self.failed_node = failed_node
        
    def __repr__(self):
        return f"Decision(action={self.action}, next_node={self.next_node}, class={self.class_name}, conf={self.confidence})"

class AutomationNode:
    """
    Base class for all classification nodes in the tree.
    """
    def __init__(self, model_path: str, node_name: str):
        self.model_path = model_path
        self.node_name = node_name
        self.model = None
        self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        self._load_lock = asyncio.Lock()

    async def _ensure_model_loaded(self):
        """Thread-safe, non-blocking model load. Subclasses with synchronous
        load_model() call this instead of calling load_model() directly, so
        the heavy init work runs in a thread pool and never blocks the Discord
        event loop or gateway heartbeat."""
        if self.model is None:
            async with self._load_lock:
                if self.model is None:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, self.load_model)

    def load_model(self):
        if self.model is None:
            logger.info(f"Loading model {self.node_name} from {self.model_path} onto {self.device}")
            self.model = YOLO(self.model_path)
            
    async def evaluate(self, image_path: str, guild_config: dict) -> Decision:
        raise NotImplementedError("Each node must implement evaluate()")
        
class AutomationTree:
    """
    The orchestrator that runs an image through the Node network.
    """
    def __init__(self):
        self.nodes = {}
        
    def add_node(self, node: AutomationNode):
        self.nodes[node.node_name] = node
        
    async def process_image(self, image_path: str, start_node_name: str, guild_config: dict, force_class: str = None) -> Decision:
        """
        Pushes an image through the decision tree until a terminal state is reached.
        If force_class is provided, it bypasses inference on the start_node and forces that class.
        """
        current_node_name = start_node_name
        
        while current_node_name:
            if current_node_name not in self.nodes:
                logger.error(f"Node {current_node_name} not found in tree!")
                return Decision(action='HITL', confidence=0.0)
                
            node = self.nodes[current_node_name]
            logger.info(f"Evaluating {image_path} via {node.node_name}")
            
            try:
                if force_class and current_node_name == start_node_name:
                    decision = node.route(force_class, 1.0, guild_config)
                else:
                    decision = await node.evaluate(image_path, guild_config)
            except Exception as e:
                logger.error(f"Node {current_node_name} failed during evaluation: {e}")
                decision = Decision(action='HITL', confidence=0.0, failed_node=current_node_name)
            
            if decision.action == 'ROUTE':
                current_node_name = decision.next_node
            else:
                decision.failed_node = current_node_name
                return decision # Terminal state (REJECT, GRANT, HITL)
                
        return Decision(action='HITL', confidence=0.0, failed_node=start_node_name) # Fallback

