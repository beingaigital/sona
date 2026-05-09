"""Session 管理：处理会话的创建、保存、加载和列表。"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.path import ensure_memory_dirs, get_stm_dir, get_task_dir


class SessionManager:
    """Session 管理器：处理会话的持久化。"""
    
    def __init__(self):
        """初始化 Session 管理器。"""
        self.stm_dir = ensure_memory_dirs()

    @staticmethod
    def _sanitize_query_for_task_id(initial_query: str, max_length: int = 48) -> str:
        """将查询文本清洗为适合作为目录名的片段。"""
        text = re.sub(r"\s+", "_", initial_query.strip())
        text = re.sub(r'[\\/:*?"<>|]+', "_", text)
        text = re.sub(r"_+", "_", text).strip("._-")
        if not text:
            return "分析任务"
        return text[:max_length].rstrip("._-") or "分析任务"

    def _generate_task_id(self, initial_query: str) -> str:
        """按 主题_序号 生成任务 ID（不再包含日期前缀）。"""
        query_part = self._sanitize_query_for_task_id(initial_query)
        base_task_id = query_part

        existing_indices: List[int] = []
        pattern = re.compile(rf"^{re.escape(base_task_id)}_(\d{{3}})\.json$")
        for session_file in self.stm_dir.glob(f"{base_task_id}_*.json"):
            match = pattern.match(session_file.name)
            if match:
                existing_indices.append(int(match.group(1)))

        next_index = (max(existing_indices) + 1) if existing_indices else 1
        return f"{base_task_id}_{next_index:03d}"
    
    def create_session(self, initial_query: str) -> str:
        """
        创建新会话。
        
        Args:
            initial_query: 初始查询
            
        Returns:
            任务 ID（session ID）
        """
        task_id = self._generate_task_id(initial_query)
        session_data = {
            "task_id": task_id,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "description": f"初始对话：{initial_query}",
            "initial_query": initial_query,
            "messages": [],
            "token_usage": {
                "total_tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "steps": []
            }
        }
        
        self.save_session(task_id, session_data)
        return task_id

    def rename_session_task(self, old_task_id: str, final_query: str) -> str:
        """按最终查询重命名任务 ID，并迁移会话与 sandbox 目录。"""
        session_data = self.load_session(old_task_id)
        if not session_data:
            return old_task_id

        final_text = (final_query or "").strip()
        if not final_text:
            return old_task_id

        new_task_id = self._generate_task_id(final_text)
        if new_task_id == old_task_id:
            session_data["initial_query"] = final_text
            self.save_session(old_task_id, session_data, final_query=final_text)
            return old_task_id

        old_session_file = self.stm_dir / f"{old_task_id}.json"
        new_session_file = self.stm_dir / f"{new_task_id}.json"
        if new_session_file.exists():
            session_data["initial_query"] = final_text
            self.save_session(old_task_id, session_data, final_query=final_text)
            return old_task_id

        old_task_dir = get_task_dir(old_task_id)
        new_task_dir = get_task_dir(new_task_id)

        if old_task_dir.exists() and not new_task_dir.exists():
            new_task_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_task_dir), str(new_task_dir))

        session_data["task_id"] = new_task_id
        session_data["initial_query"] = final_text
        session_data["updated_at"] = datetime.now().isoformat()
        if final_text:
            session_data["description"] = f"分析{final_text}"

        with open(new_session_file, "w", encoding="utf-8") as f:
            json.dump(session_data, f, ensure_ascii=False, indent=2)

        if old_session_file.exists():
            old_session_file.unlink()

        return new_task_id
    
    def save_session(
        self,
        task_id: str,
        session_data: Dict[str, Any],
        final_query: Optional[str] = None
    ) -> None:
        """
        保存会话数据
        
        Args:
            task_id: 任务 ID
            session_data: 会话数据
            final_query: 最终查询（用于更新描述）
        """
        session_data["updated_at"] = datetime.now().isoformat()
        
        # 如果提供了最终查询，更新描述
        if final_query:
            session_data["description"] = f"分析{final_query}"
        
        session_file = self.stm_dir / f"{task_id}.json"
        with open(session_file, 'w', encoding='utf-8') as f:
            json.dump(session_data, f, ensure_ascii=False, indent=2)
    
    def replace_messages(
        self,
        task_id: str,
        messages: List[Dict[str, Any]],
        reset_token_usage: bool = True
    ) -> None:
        """
        替换会话中的所有消息（用于消息压缩后更新）
        
        Args:
            task_id: 任务 ID
            messages: 新的消息列表（dict 格式）
            reset_token_usage: 是否重置 token_usage（压缩后应重置，因为旧消息的 token 不再计入上下文）
        """
        session_data = self.load_session(task_id)
        if not session_data:
            return
        
        session_data["messages"] = messages
        session_data["updated_at"] = datetime.now().isoformat()
        
        # 压缩后重置 token_usage
        if reset_token_usage:
            if "token_usage" in session_data:
                # 重置累计值，但保留 steps 记录
                session_data["token_usage"]["total_tokens"] = 0
                session_data["token_usage"]["prompt_tokens"] = 0
                session_data["token_usage"]["completion_tokens"] = 0
        
        self.save_session(task_id, session_data)
    
    def load_session(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        加载会话数据
        
        Args:
            task_id: 任务 ID
            
        Returns:
            会话数据，如果不存在则返回 None
        """
        session_file = self.stm_dir / f"{task_id}.json"
        if not session_file.exists():
            return None
        
        with open(session_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def list_sessions(self, limit: int = 5) -> List[Dict[str, Any]]:
        """
        列出最近的会话
        
        Args:
            limit: 返回的会话数量限制
            
        Returns:
            会话列表，按更新时间倒序排列
        """
        sessions = []
        
        # 遍历所有 session 文件
        for session_file in self.stm_dir.glob("*.json"):
            try:
                with open(session_file, 'r', encoding='utf-8') as f:
                    session_data = json.load(f)
                    sessions.append(session_data)
            except Exception:
                # 跳过损坏的文件
                continue
        
        # 按更新时间排序（最新的在前）
        sessions.sort(
            key=lambda x: x.get("updated_at", ""),
            reverse=True
        )
        
        return sessions[:limit]
    
    def add_message(
        self,
        task_id: str,
        role: str,
        content: str,
        tool_name: Optional[str] = None,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        tool_call_id: Optional[str] = None
    ) -> None:
        """
        向会话添加消息
        
        Args:
            task_id: 任务 ID
            role: 消息角色（user/assistant/tool）
            content: 消息内容
            tool_name: 工具名称（仅当 role 为 tool 时使用）
            tool_calls: 工具调用信息（仅当 role 为 assistant 时使用）
            tool_call_id: 工具调用 ID（仅当 role 为 tool 时使用，必须与对应的 assistant 消息中的 tool_calls[].id 匹配）
        """
        session_data = self.load_session(task_id)
        if not session_data:
            return
        
        if "messages" not in session_data:
            session_data["messages"] = []
        
        msg_data = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        }
        
        if tool_name:
            msg_data["tool_name"] = tool_name
        
        if tool_calls:
            msg_data["tool_calls"] = tool_calls
        
        if tool_call_id:
            msg_data["tool_call_id"] = tool_call_id
        
        session_data["messages"].append(msg_data)
        
        self.save_session(task_id, session_data)
    
    def add_token_usage(
        self,
        task_id: str,
        step_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int
    ) -> None:
        """
        添加 token 使用记录
        
        Args:
            task_id: 任务 ID
            step_name: 步骤名称
            prompt_tokens: Prompt tokens
            completion_tokens: Completion tokens
            total_tokens: 总 tokens
        """
        session_data = self.load_session(task_id)
        if not session_data:
            return
        
        if "token_usage" not in session_data:
            session_data["token_usage"] = {
                "total_tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "steps": []
            }
        
        token_usage = session_data["token_usage"]
        token_usage["total_tokens"] += total_tokens
        token_usage["prompt_tokens"] += prompt_tokens
        token_usage["completion_tokens"] += completion_tokens
        
        token_usage["steps"].append({
            "step_name": step_name,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "timestamp": datetime.now().isoformat()
        })
        
        self.save_session(task_id, session_data)


# 全局 Session 管理器实例
_session_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    """获取全局 Session 管理器实例。"""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager
