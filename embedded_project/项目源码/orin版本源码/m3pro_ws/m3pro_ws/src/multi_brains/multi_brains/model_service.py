
import os
import rclpy
from rclpy.node import Node
from interfaces.action import Rot
from std_msgs.msg import String,Bool
from rclpy.action import ActionClient
from ament_index_python.packages import get_package_share_directory
import time
from rclpy.logging import get_logger
from .utils.dify_client import ChatClient
from .asr_detect import ASR_Detect
import requests
from colorama import Fore
import queue
import yaml
import threading
from .utils.common_tools import extract_json_content,LogTranslator
from .utils.tts import PiperTTS, BaiduTTS, TongyiTTS, XunfeiTTS
from interfaces.msg import LlmRequest
import pygame

class Dify_LLM_Client():
    def __init__(self, api_key,
                 base_url="http://localhost/v1"):
        self.api_key = api_key
        self.base_url = base_url
        self.conversation_id = None  # 会话id
        self.client = ChatClient(api_key, base_url)
        self.logger = get_logger("dify_llm_client")

    def reset_conversation(self):
        '''重置会话ID'''
        self.conversation_id = None
    
    def get_conversation_id(self):
        '''获取会话ID'''
        return self.conversation_id

    def set_conversation_id(self, conversation_id):
        '''设置会话ID'''
        self.conversation_id = conversation_id

    def test_connection(self):
        """
        测试Dify服务连接是否正常
        Test if the Dify service connection is working
        """
        try:
            response = self.client.get_meta("yahboom")
            if response.status_code == 200:
                return [True, "Connection successful"]
            else:
                return [False, f"Connection failed with status code: {response.status_code}"]
        except requests.exceptions.RequestException as e:
            return [False, f"Connection error: {str(e)}"]
        except Exception as e:
            return [False, f"Unexpected error: {str(e)}"]

    def chat(self, input:str,image_path:str=None,robot_feedback:bool=None)-> list[bool, str]:
        '''
        请求dify服务器,获取回复
        '''
        #请求参数
        kwargs = {
            "inputs": {"robot_feedback": str(robot_feedback)},
            "query":input,
            "user":"yahboom",
            "response_mode":"blocking",
            "conversation_id":self.conversation_id
        }

        if image_path is not None:# 多模态视觉请求

            with open(image_path, "rb") as file:  
                files = {"file": ("robot-perspective-picture", file, "image/png")}
                response = self.client.file_upload("yahboom", files)
                file_id = response.json().get("id")

            image = [
                {
                    "type": "image",
                    "transfer_method": "local_file",
                    "upload_file_id": file_id,
                }
            ]
            kwargs["files"] = image
        try:
            chat_response =self.client.create_chat_message(**kwargs)
            chat_response.raise_for_status()
            result:dict = chat_response.json()
        except Exception as e:
            return [False, str(e)]
        
        if result.get("answer") is not None:
            self.conversation_id=result.get("conversation_id")
            return [True, result.get("answer")]


class Ros_LLM_Service(Node):
    def __init__(self):
        super().__init__("LargeModelService")
        

        self.init_param_config()  # 初始化参数配置 / Initialize parameter configuration
        self.init_ros_comunication()  # 初始化ROS通信 / Initialize ROS communication
        self.init_largemodel()    # 初始化大模型 / Initialize large model
        self.get_logger().info(Fore.GREEN+"ROS_LLM_Service Initialization completed"+Fore.RESET)
    def init_param_config(self):
        '''
        初始化参数配置 / Initialize parameter configuration
        '''
        self.declare_parameter("config_file", os.path.join(os.path.expanduser('~'),'M3Pro_ws','multi_brains_file','multi_brains_setting.yaml'))
        self.config_file = self.get_parameter("config_file").get_parameter_value().string_value
        self.declare_parameter("text_chat_mode", False) # 是否为文本聊天模式 / Whether it is text chat mode
        self.text_chat_mode = self.get_parameter("text_chat_mode").get_parameter_value().bool_value
        self.image_cache_path=os.path.join(os.path.expanduser('~'),'M3Pro_ws','multi_brains_file','image.png')

        with open(self.config_file, "r") as file:
                self.config_param = yaml.safe_load(file)
        self.language=self.config_param.get("LANGUAGE","zh")
        self.dify_base_url=self.config_param.get("DIFY_BASE_URL")
        self.dify_api_key=self.config_param.get("DIFY_API_KEY")
        self.debug_mode=self.config_param.get("DEBUG_MODE",False)
        self.llm_handler_queue = queue.Queue(1)#dify-agent请求处理队列
        self.pygame_lock = threading.Lock()
        self.syslog=LogTranslator(self.language)
        if not self.syslog.load_translations_file(os.path.join(get_package_share_directory("multi_brains"),"language")):
            self.get_logger().error("Failed to load language translations file")

    def init_largemodel(self):
        #初始化dify
        self.dify_llmclient=Dify_LLM_Client(api_key=self.dify_api_key,base_url=self.dify_base_url)
        result=self.dify_llmclient.test_connection()
        if result[0]:
            self.get_logger().info("Dify LLM connection successful")
        else:
            self.get_logger().error(Fore.RED+f"Dify LLM connection failed,\
                                    Please check whether DIY dify has been started, API and BASE_URL settings\
                                    Error log:{result[1]}"+Fore.RESET)
        llm_handler_thread = threading.Thread(target=self.handle_llm_thread)#启动dify线程，处理大模型请求相关的业务
        llm_handler_thread.daemon = True
        llm_handler_thread.start()

        if not self.text_chat_mode:  # 语音聊天模式
            #初始化asr
            self.asr_detect=ASR_Detect(self.llm_handler_queue,self.config_file, pygame_lock=self.pygame_lock)
            if not self.asr_detect.init_ASR_Detect() :
                self.get_logger().error(Fore.RED+"Failed to initialize ASR_Engine"+Fore.RESET)

            asr_thread = threading.Thread(target=self.asr_detect.asr_detect_run)#启动asr线程，处理唤醒、语音识别相关的业务
            asr_thread.daemon = True
            asr_thread.start()

            #初始化tts
            self.tts_out_path=os.path.join(os.path.expanduser('~'),'M3Pro_ws','multi_brains_file','tts_output.wav')  # 语音合成输出路径 / TTS output path
            
            if self.config_param.get("USE_OLINE_TTS", False):
                tts_supplier=self.config_param.get("TTS_SUPPLIER")

                if tts_supplier=="aliyun":
                    self.tts_engine=TongyiTTS(self.config_file)
                    if self.tts_engine.init_tts_engine() :
                        self.get_logger().info(Fore.BLUE+"Tongyi TTS_Engine initialized successfully"+Fore.RESET)
                    else:
                        self.get_logger().error(Fore.RED+"Tongyi TTS_Engine initialized failed"+Fore.RESET)
                elif tts_supplier=="baidu":
                    self.tts_engine=BaiduTTS(self.config_file)
                    if self.tts_engine.init_tts_engine() :
                        self.get_logger().info(Fore.BLUE+"Baidu TTS_Engine initialized successfully"+Fore.RESET)
                    else:
                        self.get_logger().error(Fore.RED+"Baidu TTS_Engine initialized failed "+Fore.RESET)

                elif tts_supplier=="xunfei":
                    self.tts_out_path=os.path.join(os.path.expanduser('~'),'M3Pro_ws','multi_brains_file','xunfei_tts_output.mp3') 
                    key_path=os.path.join(os.path.expanduser('~'),self.config_param.get("XUNFEI_KEY",""))
                    xunfei_encrypted_key=os.path.join(os.path.expanduser('~'),self.config_param.get("XUNFEI_ENCRYPTED",""))
                    self.tts_engine=XunfeiTTS(key_path,xunfei_encrypted_key,self.config_file)

                    if self.tts_engine.init_tts_engine():
                        self.get_logger().info(Fore.BLUE+"Xunfei TTS_Engine initialized successfully"+Fore.RESET)
                    else:
                        self.get_logger().error(Fore.RED+"Xunfei TTS_Engine initialized failed"+Fore.RESET)

            else:
                self.tts_engine=PiperTTS(self.language)
                if self.tts_engine.init_tts_engine() :
                    self.get_logger().info(Fore.BLUE+"Piper TTS_Engine initialized successfully"+Fore.RESET)
                else:
                    self.get_logger().error(Fore.RED+"Piper TTS_Engine initialized failed"+Fore.RESET)
                    
    def init_ros_comunication(self):
        # 创建动作客户端，连接到 'action_service' / Create action client, connect to 'action_service'
        self._action_client = ActionClient(self, Rot, "/action_service")
        # 创建文字交互发布者 / Create text interaction publisher
        self.text_pub = self.create_publisher(String, "text_response", 1)
        self.llm_request_sub = self.create_subscription(LlmRequest, "llm_request_handler", self.llm_request_callback, 5)


    def llm_request_callback(self, msg:LlmRequest):
        '''
        话题回调函数,接收调用模型请求并放入队列中 / Topic callback function, receive model request and put into queue
        '''
        if self.debug_mode: self.get_logger().info(f"robot_feedback:{msg.robot_feedback},llm_request:{msg.llm_request}")
        if msg.robot_feedback:
            # 机器人反馈请求
            if msg.llm_request ==self.syslog.get_text("image_feedback"):
                self.llm_handler_queue.put([msg.llm_request,'image_request',True])
            elif msg.llm_request =="finish":
                # 收到dify-agent的finish指令，结束当前任务周期，这会清空历史上下文，并开始新的任务周期
                self.clear_request_queue()  # 清空请求队列
                self.dify_llmclient.reset_conversation()  # 重置会话
            else:
                #常规机器人反馈结果
                if self.debug_mode: self.get_logger().info(self.syslog.get_text("system_log_4"))
                self.llm_handler_queue.put([msg.llm_request,'text_request',True])
        
        else:# 其他来源的模型请求
            self.llm_handler_queue.put([msg.llm_request,'text_request',None])

    def handle_llm_thread(self)->None:
        '''
        处理模型请求/ Handle model request
        '''
        while True:
            if not self.llm_handler_queue.empty():#队列不为空,处理模型请求
                if  not self.text_chat_mode and self.asr_detect.record_flag : continue
                request_query = self.llm_handler_queue.get()
                if self.debug_mode: self.get_logger().info(f"Processing LLM request: {request_query}")

                if request_query[1]=='text_request':
                    '''text request'''
                    result=self.dify_llmclient.chat(request_query[0],robot_feedback=request_query[2])
                elif request_query[1]=='image_request':
                    '''vision + text request'''
                    result=self.dify_llmclient.chat(request_query[0],image_path=self.image_cache_path,robot_feedback=request_query[2])
            
            
                if result[0]:
                    if  not self.text_chat_mode and self.asr_detect.record_flag : continue
                    split_result=self.extract_actions(result[1])
                    if split_result is None: continue
                    action_list,llm_response,decision_plan=self.extract_actions(result[1])
                    if decision_plan is not None: 
                        self.get_logger().info(Fore.YELLOW+self.syslog.get_text("system_log_3",decision_plan=decision_plan)+Fore.RESET)
                    self.get_logger().info(Fore.YELLOW+f'"action": {action_list},"response": {llm_response}'+Fore.RESET) 

                    if  not self.text_chat_mode:#语音回复
                        if self.tts_engine.synthesize(llm_response,self.tts_out_path) :
                            self.play_audio(self.tts_out_path)
                        else:
                            self.get_logger().error(Fore.RED+"Speech synthesis failed. Check whether the TTS model is available"+Fore.RESET)
                    else:#文本回复
                        if decision_plan is not None: 
                            self.text_pub.publish(String(data=self.syslog.get_text("system_log_3",decision_plan=decision_plan)))
                        self.text_pub.publish(String(data=f'"action": {action_list}, "response": {llm_response}'))

                    if action_list!=[]: self.send_action_service(action_list, llm_response)  
                else:
                    self.get_logger().error(Fore.RED+f"The model request failed. Check whether the dify or AI model is normal.\
                                            Error Log:{result[1]}"+Fore.RESET)
            else:  
                time.sleep(1.0)#无请求时休眠1秒

    def send_action_service(self, actions: list[str], text: str):
        '''异步发送动作列表、回复内容给ActionServer / Asynchronously send action list and response content to ActionServer'''
        goal_msg = Rot.Goal()  
        goal_msg.actions = actions  
        goal_msg.llm_response = text
        self._send_goal_future = self._action_client.send_goal_async(goal_msg)
        self._send_goal_future.add_done_callback(self.goal_response_callback) # 添加目标发送后的响应回调函数 / Add response callback function after sending the goal

    def goal_response_callback(self, future):
        goal_handle = future.result()  # 获取目标句柄 / Get goal handle
        if not goal_handle.accepted:
            self.get_logger().info( "action_client message: action service rejected action list")  # 目标被拒绝...

    def clear_request_queue(self):
        '''清空请求队列'''
        while not self.llm_handler_queue.empty():
            self.llm_handler_queue.get()

    def play_audio(self,file_path: str) -> None:
        '''播放音频 / Play audio'''
        self.asr_detect.extern_wakeup.clear()
        with self.pygame_lock:
            pygame.mixer.init()
            pygame.mixer.music.load(file_path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                if self.asr_detect.extern_wakeup.is_set():
                    pygame.mixer.music.stop()
                    self.asr_detect.extern_wakeup.clear()
                    break
                pygame.time.Clock().tick(10)
            pygame.mixer.quit()

    def extract_actions(self,llm_result:str):
        '''提取动作列表和对话 / Extract action list and text content'''
        try:
            parts = llm_result.split('&&&&', maxsplit=1)
            json_str = extract_json_content(parts[0])
            action_list = json_str.get("action", [])
            llm_response = json_str.get("response", "")
            if parts[1] != "":
                return action_list, llm_response, parts[1]
            return action_list, llm_response, None
        except Exception as e:
            self.get_logger().error(Fore.RED+f"Failed to extract JSON content Error Log:\
                                    {str(e)}"+Fore.RESET)
            return None

def main(args=None):
    rclpy.init(args=args)
    model_service = Ros_LLM_Service()
    rclpy.spin(model_service)
    model_service.destroy_node()
    rclpy.shutdown()

if __name__ == "__main__":
    main()

