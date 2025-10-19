import threading
from flask import Flask, request, jsonify
from datetime import datetime
from shared.logger import setup_logger

class AgentServer:
       def __init__(self, event_handler, port=8080):
           self.event_handler = event_handler
           self.port = port
           self.logger = setup_logger(__name__)
           self.app = Flask(__name__)
           self._setup_routes()
       
       def _setup_routes(self):
           @self.app.route('/api/agent/close-session', methods=['POST'])
           def close_session():
               try:
                   data = request.json
                   session_id = data.get('session_id')
                   file_path = data.get('file_path')
                   username = data.get('username')
                   ended_at = data.get('ended_at')
           
                   self.logger.info(f"🔄 Received close-session command: {file_path} by {username}")
           
                   if file_path and username:
                       session_key = f"{file_path}:{username}"
                       if session_key not in self.event_handler.session_manager.active_sessions:
                           self.logger.info(f"ℹ️ Session already closed or not found: {file_path} by {username}")
                           return jsonify({
                               "status": "already_closed", 
                               "session_id": session_id,
                               "file_path": file_path
                           })
               
                       closed_session = self.event_handler.session_manager.close_session(file_path, username)
               
                       if closed_session:
                           if ended_at:
                               closed_session['ended_at'] = datetime.fromisoformat(ended_at)
                       
                           self.logger.info(f"✅ Session closed via server command: {file_path}")
                           return jsonify({
                               "status": "closed", 
                               "session_id": session_id,
                               "file_path": file_path
                           })
               
                   return jsonify({"status": "not_found"}), 404
           
               except Exception as e:
                   self.logger.error(f"❌ Error processing close-session: {e}")
                   return jsonify({"status": "error", "message": str(e)}), 500
           
           @self.app.route('/api/agent/comment-created', methods=['POST'])
           def comment_created():
               try:
                   data = request.json
                   session_id = data.get('session_id')
                   file_path = data.get('file_path')
                   username = data.get('username')
                   comment_data = data.get('comment', {})
                   
                   self.logger.info(f"💬 Received comment notification: {file_path} by {username}")
                   
                   if file_path:
                       self.event_handler.commented_files[file_path] = {
                           'commented_at': datetime.now(),
                           'username': username,
                           'session_id': session_id,
                           'content': comment_data.get('content', ''),
                           'change_type': comment_data.get('change_type', 'other'),
                           'created_at': comment_data.get('created_at', datetime.now().isoformat())
                       }
                       
                       if file_path and username:
                           self.event_handler.session_manager.close_session(file_path, username)
                       
                       self.logger.info(f"✅ Comment tracked for file: {file_path}")
                       return jsonify({"status": "processed", "file_path": file_path})
                   
                   return jsonify({"status": "invalid_data"}), 400
               
               except Exception as e:
                   self.logger.error(f"❌ Error processing comment: {e}")
                   return jsonify({"status": "error", "message": str(e)}), 500
           
           @self.app.route('/api/agent/health', methods=['GET'])
           def health():
               return jsonify({
                   "status": "healthy", 
                   "service": "monitoring-agent",
                   "timestamp": datetime.now().isoformat()
               })

           @self.app.route('/api/agent/active-sessions', methods=['GET'])
           def get_active_sessions():
               try:
                   sessions = [
                       {
                           "session_id": session_data["session_id"],
                           "file_path": session_data["file_path"],
                           "username": session_data["username"],
                           "started_at": session_data["started_at"].isoformat(),
                           "last_activity": session_data["last_activity"].isoformat(),
                           "hash_before": session_data.get("hash_before"),
                           "resume_count": session_data.get("resume_count", 0),
                           "is_commented": session_data.get("is_commented", False)
                       }
                       for session_data in self.event_handler.session_manager.active_sessions.values()
                   ]
                   self.logger.info("Returning active sessions list")
                   return jsonify({"status": "success", "sessions": sessions})
               except Exception as e:
                   self.logger.error(f"Error fetching active sessions: {e}")
                   return jsonify({"status": "error", "message": str(e)}), 500
       
       def start(self):
           try:
               thread = threading.Thread(
                   target=lambda: self.app.run(
                       host='0.0.0.0', 
                       port=self.port, 
                       debug=False, 
                       use_reloader=False
                   ),
                   daemon=True
               )
               thread.start()
               self.logger.info(f"🎯 Agent server started on port {self.port}")
           except Exception as e:
               self.logger.error(f"❌ Failed to start agent server: {e}")