import queue
import os
import time
import pyaudio
import wave
import threading
import webrtcvad
from ament_index_python.packages import get_package_share_directory
from rclpy.logging import get_logger
import yaml
from colorama import Fore
import random
from .utils.asr import Tongyi_ASR,SenseVoiceSmall_ASR,XUNFEI_ASR
from .utils.common_tools import *
from .utils.mic_serial import kws_mic
from rclpy.node import Node
from std_msgs.msg import  Bool
import pygame
from collections import deque
class Asr_Ros_Controller(Node):
    def __init__(self):
        super().__init__("asr_ros_controller")
        self.wakeup_pub = self.create_publisher(Bool, "wakeup_event", 3)


class ASR_Detect():
    def __init__(self,
                 asr_result_queue:queue,
                 config_file:str=os.path.join(os.path.expanduser('~'),'M3Pro_ws','multi_brains_file','multi_brains_setting.yaml'),
                 pygame_lock=None):
        self.config_file=config_file
        self.asr_result_queue=asr_result_queue
        self.logger = get_logger("ASR_Detect")
        self.publisher = Asr_Ros_Controller()
        self.extern_wakeup = threading.Event()
        self.pygame_lock = pygame_lock if pygame_lock else threading.Lock()
        self.pyaudio = pyaudio.PyAudio()
        

    def init_ASR_Detect(self):
        self.init_param()# 初始化参数、对象和变量  / Initialize parameters、objects and variables
        if not self.kws_init(): #初始化语音唤醒模块  / Initialize the voice wake-up module
            return False
        if not self.system_sound_init():  #初始化系统回复声音 / Initialize system reply sound
            return False
        if not self.asr_init():#初始化asr模型 / Initialize ASR model
            return False
        return True

    def init_param(self):
        '''初始化对象和变量 / Initialize objects and variables'''
        with open(self.config_file, "r") as file:
            config_param = yaml.safe_load(file)
        # 初始化参数 / Initialize parameters
        self.vad_mode =config_param.get("VAD_MODE",1)
        self.sample_rate =config_param.get("SAMPLE_RATE",16000)
        self.frame_ms=config_param.get("FRAME_MS",30)
        self.mic_index=config_param.get("MIC_INDEX",0)
        self.mic_serial_port=config_param.get("MIC_SERIAL_PORT","/dev/mic")
        self.asr_threashold=config_param.get("ASR_THREASHOLD",3)
        self.wakeup_threashold=config_param.get("WAKEUP_THREASHOLD",2.0)
        self.asr_supplier=config_param.get("ASR_SUPPLIER","aliyun")
        self.use_oline_asr=config_param.get("USE_OLINE_ASR",True)   
        self.asr_model=config_param.get("OLINE_ASR_MODEL","paraformer-realtime-v2")
        self.aliyun_api=config_param.get("ALIYUN_API_KEY","")
        self.xunfei_key=config_param.get("XUNFEI_KEY","")
        self.xunfei_encrypted_key=config_param.get("XUNFEI_ENCRYPTED","")
        self.language=config_param.get("LANGUAGE","zh")
        self.MAX_SILENCE_FRAMES=config_param.get("MAX_SILENCE_FRAMES",30)
        self.user_speech_dir=os.path.join(os.path.expanduser('~'),'M3Pro_ws','multi_brains_file','user_speech.wav')
        
        # 初始化对象和变量 / Initialize objects and variables
        self.vad = webrtcvad.Vad()       
        self.vad.set_mode(self.vad_mode)
        self.current_thread = None          # 唤醒处理线程 / Thread for handling wake-up events
        self.stop_event = threading.Event() #   Used to stop wake-up handling thread
        self.frame_bytes = int( self.sample_rate * self.frame_ms / 1000) 
        self.record_flag=False # record flag

    def kws_init(self):  
        '''初始化关键词唤醒相关的内容 / Initialize keyword spotting (KWS) related content'''
        self.port_name = self.mic_serial_port
        self.wakeup_event = threading.Event()
        self.serial_port = kws_mic(
                port=self.mic_serial_port,
                event=self.wakeup_event,
                baudrate=115200)    

        self.serial_port.open()
        if not self.serial_port.ser or not self.serial_port.ser.is_open:
            self.logger.fatal(Fore.RED+"Failed to open kws serial port.Please check whether the voice module is normal?"+Fore.RESET)
            return False
        receive_thread = threading.Thread(target=self.serial_port.receive_data)
        receive_thread.daemon = True
        receive_thread.start()
        return True
    def system_sound_init(self):
        '''
        初始化系统声音相关的功能,自动加载目录下所有唤醒词音频文件
        Initialize system sound functionality,Automatically load all wake-up word audio files in the directory
        ''' 

        self.notify_audio=os.path.join(get_package_share_directory("multi_brains"), "system_vioce","notify.mp3")#录音开始提示音，固定音效
        self.wakeup_audio_list = []
        self.error_audio_list = []
        if self.language=="zh":
            wakeup_dir = os.path.join(get_package_share_directory("multi_brains"), "system_vioce","zh","wakeup")
            error_wakeup_dir = os.path.join(get_package_share_directory("multi_brains"), "system_vioce","zh","error")
        elif self.language=="en":
            wakeup_dir = os.path.join(get_package_share_directory("multi_brains"), "system_vioce","en","wakeup")
            error_wakeup_dir = os.path.join(get_package_share_directory("multi_brains"), "system_vioce","en","error")
        try:
            for file in os.listdir(wakeup_dir):
                if file.endswith(".wav"):
                    self.wakeup_audio_list.append(os.path.join(wakeup_dir, file))

            for file in os.listdir(error_wakeup_dir):
                if file.endswith(".wav"):
                    self.error_audio_list.append(os.path.join(error_wakeup_dir, file))
            return True
        except:
            self.logger.error(Fore.RED+"Failed to load the wake-up word audio file"+Fore.RESET)
            return False

    def asr_detect_run(self):
        while True:
            # 只处理最近的一次唤醒请求，防止短时间重复唤醒 / Process only the most recent wake-up request to prevent duplicates
            if self.wakeup_event.wait(timeout=0.1):
                self.wakeup_event.clear()
                self.extern_wakeup.set()
                self.publisher.wakeup_pub.publish(Bool(data=True))
                
                self.logger.info("I'm here🌟")
                self.wake_up_voice() # 应答用户 / Respond to the user
                if self.current_thread and self.current_thread.is_alive():   # 打断上次的唤醒处理线程 / Interrupt the previous wake-up handling thread
                    self.stop_event.set()
                    self.current_thread.join()  # 等待当前线程结束 / Wait for the current thread to finish
                    self.stop_event.clear()  # 清除事件 / Clear the event
                self.current_thread = threading.Thread(target=self.kws_handler)
                self.current_thread.daemon = True
                self.current_thread.start()
            time.sleep(0.5)

    def kws_handler(self) -> None:
        '''唤醒处理函数 / Wake-up handling function'''
        if self.stop_event.is_set():
            self.logger.info("Wake-up processing thread interrupted  no handle user speech.!!!!!")
            return
        if self.listen_for_speech():
            asr_result=self.asr_engine.recognize(self.user_speech_dir)  # 进行 ASR 转换 / Perform ASR conversion
            if not asr_result[0]:
                self.logger.error(Fore.RED+f"Speech recognition failed because the audio segment is empty or the speech model is unavailable.ASR_OUT:{asr_result[1]}"+Fore.RESET)
            else:
                if len(asr_result[1]) > self.asr_threashold:
                    self.logger.info(Fore.GREEN+"ASR Result: "+asr_result[1]+Fore.RESET)
                    self.asr_result_queue.put([asr_result[1],'text_request',False])# 将语音识别结果放入队列中 / Put the ASR result into the queue
                else:
                    self.logger.info(Fore.YELLOW+"The voice recognition result is too short. Could it be that the user woke it up by mistake "+asr_result[1]+Fore.RESET)

    def asr_init(self): 
        '''初始化asr模型 / Initialize ASR model'''
        if self.use_oline_asr:#使用在线asr/use online asr
            if self.asr_supplier=="aliyun":#阿里云通义供应商/aliyun supplier
                self.asr_engine=Tongyi_ASR(model_name=self.asr_model,sample_rate=self.sample_rate,API=self.aliyun_api)

                if self.asr_engine.init_asr_engine():
                    self.logger.info(Fore.BLUE+f"{self.asr_model} ASR_Engine initialized successfully"+Fore.RESET)
                    return True
                else:
                    self.logger.error(Fore.RED+f"{self.asr_model} ASR_Engine initialized failed"+Fore.RESET)
                    return False
            
            if self.asr_supplier=="xunfei":#讯飞供应商/xunfei supplier
                self.asr_engine=XUNFEI_ASR(
                    key_path=os.path.join(os.path.expanduser('~'),self.xunfei_key),
                    encrypted_path=os.path.join(os.path.expanduser('~'),self.xunfei_encrypted_key)
                    )
                
                if self.asr_engine.init_asr_engine():
                    self.logger.info(Fore.BLUE+f"XunFei ASR_Engine XunFei ASR_Engine initialized successfully"+Fore.RESET)
                    return True
                else:
                    self.logger.error(Fore.RED+f"XunFei ASR_Engine XunFei ASR_Engine initialized failed"+Fore.RESET)
                    return False

        else:#使用本地asr模型/use local asr model
            self.asr_engine=SenseVoiceSmall_ASR(model_path=os.path.join(os.path.expanduser('~'),'MODELS','asr','SenseVoiceSmall'),
                                                language=self.language)
            if self.asr_engine.init_asr_engine():
                self.logger.info(Fore.BLUE+f"SenseVoiceSmall ASR_Engine initialized successfully"+Fore.RESET)
                return True
            else:
                self.logger.error(Fore.RED+f"SenseVoiceSmall ASR_Engine initialized failed"+Fore.RESET)
                return False

    def wake_up_voice(self,response="normal"):
        """播放随机唤醒音频"""
        if response == "normal":
            audio_file = random.choice(self.wakeup_audio_list)
            self.play_audio(audio_file)
        else :#播放错误唤醒音频
            audio_file = random.choice(self.error_audio_list )
            self.play_audio(audio_file)

    def listen_for_speech(self):
        '''VAD动态录音  Dynamic recording with VAD'''
        self.record_flag = True
        PRE_SPEECH_FRAMES  = 5         # 150ms 语音起始补偿
        PRINT_EVERY_N_FRAMES = 5 

        recording_active = False
        silence_counter  = 0
        print_counter    = 0

        audio_buffer = []
        pre_speech_buffer = deque(maxlen=PRE_SPEECH_FRAMES)
        stream = self.pyaudio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self.sample_rate,
            input=True,
            frames_per_buffer=self.frame_bytes,
        )
        self.play_audio(self.notify_audio)  
        try:
            while not self.stop_event.is_set():
                frame = stream.read( self.frame_bytes, exception_on_overflow=False)
                is_speech = self.vad.is_speech(frame, self.sample_rate)
                print_counter += 1
                if print_counter >= PRINT_EVERY_N_FRAMES:
                    print("1-1-1" if is_speech else "-----")
                    print_counter = 0
                if not recording_active:
                    # ---- IDLE ----
                    pre_speech_buffer.append(frame)
                    if is_speech:
                        #  RECORDING
                        recording_active = True
                        audio_buffer.extend(pre_speech_buffer)
                        pre_speech_buffer.clear()
                        audio_buffer.append(frame)
                        silence_counter = 0
                else:
                    # ---- RECORDING ----
                    audio_buffer.append(frame)
                    if is_speech:
                        silence_counter = max(0, silence_counter - 1)
                    else:
                        silence_counter += 1
                        if silence_counter >= self.MAX_SILENCE_FRAMES:
                            break
        finally:
            stream.stop_stream()
            stream.close()
            self.record_flag = False

        if recording_active and audio_buffer:
            with wave.open(self.user_speech_dir, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(self.pyaudio.get_sample_size(pyaudio.paInt16) )
                wf.setframerate(self.sample_rate)
                wf.writeframes(b"".join(audio_buffer))
            return True
        return False

    def play_audio(self,file_path: str) -> None:
        with self.pygame_lock:
            pygame.mixer.init()
            pygame.mixer.music.load(file_path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                pygame.time.Clock().tick(10)
            pygame.mixer.quit()
            
    def __del__(self):
        if self.pyaudio:
            self.pyaudio.terminate()

