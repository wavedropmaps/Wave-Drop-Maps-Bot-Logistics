import logging
import torch
from ultralytics import YOLO
from torchvision import transforms
from PIL import Image
from .automation_tree import AutomationNode, Decision
import safetensors.torch

logger = logging.getLogger('ProofNodes')

class YOLOAutomationNode(AutomationNode):
    """Base node for Ultralytics YOLO models."""
    async def evaluate(self, image_path: str, guild_config: dict) -> Decision:
        await self._ensure_model_loaded()
        results = self.model(image_path, verbose=False)
        probs = results[0].probs
        top1 = probs.top1
        confidence = float(probs.top1conf.item())
        class_name = self.model.names[top1]
        
        return self.route(class_name, confidence, guild_config)
        
    def route(self, class_name: str, confidence: float, guild_config: dict) -> Decision:
        raise NotImplementedError()

class Model1Gatekeeper(YOLOAutomationNode):
    def route(self, class_name: str, confidence: float, guild_config: dict) -> Decision:
        if confidence < 0.99:
            return Decision('HITL', confidence=confidence, class_name=class_name)
            
        if class_name == 'Garbage':
            return Decision('REJECT_DYNAMIC', message_key='Garbage', confidence=confidence, class_name=class_name)
        elif class_name == 'invite':
            return Decision('REJECT_INVITE', confidence=confidence, class_name=class_name)
        elif class_name == 'Twitter':
            if not guild_config.get('twitter_enabled', True):
                return Decision('REJECT_DYNAMIC', message_key='Garbage', confidence=confidence, class_name=class_name)
            return Decision('ROUTE', next_node='Model2_TwitterRouter', confidence=confidence, class_name=class_name)
        elif class_name == 'Creator Code':
            return Decision('ROUTE', next_node='Model5_UIRouter', confidence=confidence, class_name=class_name)
            
        return Decision('HITL', confidence=confidence, class_name=class_name)

class Model2TwitterRouter(YOLOAutomationNode):
    def route(self, class_name: str, confidence: float, guild_config: dict) -> Decision:
        if confidence < 0.99:
            return Decision('HITL', confidence=confidence, class_name=class_name)
            
        if class_name.lower() == 'desktop':
            return Decision('ROUTE', next_node='Model4_DesktopCheck', confidence=confidence, class_name=class_name)
        elif class_name.lower() == 'mobile':
            return Decision('ROUTE', next_node='Model3a_MobileCheck1', confidence=confidence, class_name=class_name)
            
        return Decision('HITL', confidence=confidence, class_name=class_name)

class Model3aMobileCheck1(YOLOAutomationNode):
    def route(self, class_name: str, confidence: float, guild_config: dict) -> Decision:
        if confidence < 0.99:
            return Decision('HITL', confidence=confidence, class_name=class_name)
            
        if class_name == 'Following only':
            return Decision('REJECT_FOLLOWING_ONLY', confidence=confidence, class_name=class_name)
        elif class_name == 'either':
            return Decision('ROUTE', next_node='Model3b_MobileCheck2', confidence=confidence, class_name=class_name)
            
        return Decision('HITL', confidence=confidence, class_name=class_name)

class Model4DesktopCheck(YOLOAutomationNode):
    def route(self, class_name: str, confidence: float, guild_config: dict) -> Decision:
        if confidence < 0.99:
            return Decision('HITL', confidence=confidence, class_name=class_name)
            
        if class_name == 'Following only':
            return Decision('REJECT_FOLLOWING_ONLY', confidence=confidence, class_name=class_name)
        elif class_name == 'Liking only':
            return Decision('REJECT_LIKING_ONLY', confidence=confidence, class_name=class_name)
        elif class_name == 'scam':
            return Decision('REJECT_DYNAMIC', message_key='Garbage', confidence=confidence, class_name=class_name)
        elif class_name == 'Following and liking':
            return Decision('GRANT_LEVEL_1', confidence=confidence, class_name=class_name)
            
        return Decision('HITL', confidence=confidence, class_name=class_name)

class Model5UIRouter(YOLOAutomationNode):
    def route(self, class_name: str, confidence: float, guild_config: dict) -> Decision:
        if confidence < 0.99:
            return Decision('HITL', confidence=confidence, class_name=class_name)
            
        if class_name == 'Online fort website' or class_name == 'Iphone Shop':
            return Decision('GRANT_LEVEL_2', confidence=confidence, class_name=class_name)
        elif class_name == 'Taken via phone':
            return Decision('ROUTE', next_node='Model6_PhonePhoto', confidence=confidence, class_name=class_name)
        elif class_name == 'ScreenShot':
            return Decision('ROUTE', next_node='Model7_Screenshot', confidence=confidence, class_name=class_name)
            
        return Decision('HITL', confidence=confidence, class_name=class_name)

class Model6PhonePhoto(YOLOAutomationNode):
    def route(self, class_name: str, confidence: float, guild_config: dict) -> Decision:
        if confidence < 0.99:
            return Decision('HITL', confidence=confidence, class_name=class_name)
            
        if class_name == 'Press search':
            return Decision('REJECT_PRESS_SEARCH', confidence=confidence, class_name=class_name)
        elif class_name == 'zoom out':
            return Decision('REJECT_ZOOM_OUT', confidence=confidence, class_name=class_name)
        elif class_name == 'using code correctly':
            return Decision('GRANT_LEVEL_2', confidence=confidence, class_name=class_name)
            
        return Decision('HITL', confidence=confidence, class_name=class_name)

class Model7Screenshot(YOLOAutomationNode):
    def route(self, class_name: str, confidence: float, guild_config: dict) -> Decision:
        if confidence < 0.99:
            return Decision('HITL', confidence=confidence, class_name=class_name)
            
        if class_name == 'Need to press search':
            return Decision('REJECT_PRESS_SEARCH', confidence=confidence, class_name=class_name)
        elif class_name == 'Zoom out':
            return Decision('REJECT_ZOOM_OUT', confidence=confidence, class_name=class_name)
        elif class_name == 'Correctly using code':
            return Decision('GRANT_LEVEL_2', confidence=confidence, class_name=class_name)
            
        return Decision('HITL', confidence=confidence, class_name=class_name)

class ViTAutomationNode(AutomationNode):
    """Node for HuggingFace ViT models."""
    def __init__(self, model_path: str, node_name: str, class_names: list):
        super().__init__(model_path, node_name)
        self.class_names = class_names
        self.transform = transforms.Compose([
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5]),
        ])
        
    def load_model(self):
        if self.model is None:
            from transformers import ViTForImageClassification
            self.model = ViTForImageClassification.from_pretrained('google/vit-base-patch16-224-in21k', num_labels=len(self.class_names), ignore_mismatched_sizes=True)
            
            state_dict = safetensors.torch.load_file(self.model_path)
            
            # Map older transformer keys (e.g. attention.query) to new unified ViT keys (e.g. q_proj)
            new_state_dict = {}
            for k, v in state_dict.items():
                new_k = k
                if "vit.encoder.layer." in new_k:
                    new_k = new_k.replace("vit.encoder.layer.", "vit.layers.")
                    new_k = new_k.replace(".attention.attention.query.", ".attention.q_proj.")
                    new_k = new_k.replace(".attention.attention.key.", ".attention.k_proj.")
                    new_k = new_k.replace(".attention.attention.value.", ".attention.v_proj.")
                    new_k = new_k.replace(".attention.output.dense.", ".attention.o_proj.")
                    new_k = new_k.replace(".intermediate.dense.", ".mlp.fc1.")
                    new_k = new_k.replace(".output.dense.", ".mlp.fc2.")
                new_state_dict[new_k] = v
                
            # strict=False allows some flexible loading, but we should see a clean load now
            self.model.load_state_dict(new_state_dict, strict=False)
            self.model.eval()
            self.model.to(self.device)
            logger.info(f"Loaded ViT {self.node_name} onto {self.device}")

    async def evaluate(self, image_path: str, guild_config: dict) -> Decision:
        await self._ensure_model_loaded()
        img = Image.open(image_path).convert('RGB')
        pv = self.transform(img).unsqueeze(0).to(self.device)
        
        with torch.no_grad():
            logits = self.model(pixel_values=pv).logits
            probs = torch.softmax(logits, dim=-1).squeeze()
            
        top1 = probs.argmax().item()
        confidence = float(probs[top1].item())
        class_name = self.class_names[top1]
        
        return self.route(class_name, confidence, guild_config)
        
    def route(self, class_name: str, confidence: float, guild_config: dict) -> Decision:
        raise NotImplementedError()

class Model3bMobileCheck2(ViTAutomationNode):
    def route(self, class_name: str, confidence: float, guild_config: dict) -> Decision:
        if confidence < 0.70:
            return Decision('HITL', confidence=confidence, class_name=class_name)
            
        if class_name == 'Following only':
            return Decision('REJECT_FOLLOWING_ONLY', confidence=confidence, class_name=class_name)
        elif class_name == 'Liking only':
            return Decision('REJECT_LIKING_ONLY', confidence=confidence, class_name=class_name)
        elif class_name == 'scam':
            return Decision('REJECT_DYNAMIC', message_key='Garbage', confidence=confidence, class_name=class_name)
        elif class_name == 'Following and liking':
            return Decision('GRANT_LEVEL_1', confidence=confidence, class_name=class_name)
            
        return Decision('HITL', confidence=confidence, class_name=class_name)
