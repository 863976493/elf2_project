from abc import ABC, abstractmethod
from dashscope.audio.asr import Recognition
from http import HTTPStatus
from dashscope.audio.asr import Recognition
from dashscope.audio.asr import *
import dashscope
from funasr import AutoModel
from datetime import datetime
import websocket
from cryptography.fernet import Fernet
import json
import hashlib
import base64
import hmac
import ssl
from wsgiref.handlers import format_date_time
from urllib.parse import urlencode
from time import mktime
import time
import threading

class ASR(ABC):
    '''抽象类,所有ASR接口类的基类'''
    @abstractmethod
    def recognize(self,audio_path):
        """识别音频数据并返回文本"""
        pass
    @abstractmethod
    def init_asr_engine(self):
        """初始化ASR引擎"""
        pass



class Tongyi_ASR(ASR):
    '''百炼大模型——通义千问ASR接口类'''
    def __init__(self,
                 model_name:str,
                 sample_rate:int,
                 API:str,
                 language:str = 'zh'
                 ):
        self.asr_model = model_name
        self.sample_rate = sample_rate
        self.language = language
        self.API = API
    def init_asr_engine(self)->bool:
        dashscope.api_key = self.API
        if self.asr_model not in [
            "paraformer-realtime-v2",
            "paraformer-realtime-v1",
            "paraformer-realtime-8k-v2",
            "paraformer-realtime-8k-v1",
            "gummy-realtime-v1",
            "gummy-chat-v1",
        ]: return False
        return True

    def recognize(self, audio_path:str)->list:
        if self.asr_model in [
            "paraformer-realtime-v2",
            "paraformer-realtime-v1",
            "paraformer-realtime-8k-v2",
            "paraformer-realtime-8k-v1", ]:
            recognition = Recognition(
                model=self.asr_model,
                format="wav",
                sample_rate=self.sample_rate,
                callback=None,
            )
            try:
                result = recognition.call(audio_path)
                sentences = result.get_sentence()
                if result.status_code == HTTPStatus.OK and isinstance(sentences, list):
                    return [True,sentences[0].get("text", "")]
                else:
                    return [False, " asr recognize failure"]
            except Exception as e:
                return [False, str(e)]
            
        elif  self.asr_model in ["gummy-realtime-v1","gummy-chat-v1"]:

            translator = TranslationRecognizerRealtime(
                model=self.asr_model,
                format="wav",
                sample_rate=self.sample_rate,
                translation_enabled=True,
                callback=None,
            )
            try:
                result = translator.call(audio_path)
                if not result.error_message:
                    output = ""
                    for transcription_result in result.transcription_result_list:
                        output += transcription_result.text
                    return [True, output]
                else:
                    return [False, result.error_message]
            except Exception as e:
                return [False, str(e)]


class SenseVoiceSmall_ASR(ASR):
    '''本地语音识别模型ASR接口类'''
    def __init__(self,model_path:str,language:str):
        self.model_path = model_path
        self.language=language
    def init_asr_engine(self)->bool:
        try:
            self.model_senceVoice = AutoModel(
            model= self.model_path,
            trust_remote_code=False,
            disable_update=True)
            return True
        except :
            return False 

    def recognize(self, audio_path:str)->list:
        try:
            res = self.model_senceVoice.generate(
                input=audio_path,
                cache={},
                language=self.language,  # "zn", "en", "yue", "ja", "ko", "nospeech"
                use_itn=False,
            )
            prompt = res[0]["text"].split(">")[-1]
            return [True,prompt]
        except:
            return [False,""]


class XUNFEI_ASR(ASR):
    '''科大讯飞ASR接口类'''
    def __init__(self,key_path:str,encrypted_path:str):
        self.key_path = key_path
        self.encrypted_path = encrypted_path
        self.STATUS_FIRST_FRAME = 0  # 第一帧的标识
        self.STATUS_CONTINUE_FRAME = 1  # 中间帧标识
        self.STATUS_LAST_FRAME = 2  # 最后一帧的标识
        self.wsParam = ""
        self.result_text = ""  # 存储识别结果
        
    def init_asr_engine(self)->bool:
        # 加载加密密钥
        try:
            with open(self.key_path, "rb") as f:
                encryption_key = f.read()
            # 解密配置
            cipher = Fernet(encryption_key)
            with open(self.encrypted_path, "rb") as f:
                encrypted_secrets = f.read()
            secrets = json.loads(cipher.decrypt(encrypted_secrets).decode())
            self.APPID=secrets["APPID"]
            self.APISecret=secrets["APISecret"]
            self.APIKey=secrets["APIKey"]
            return True
        except :
            return False

    def recognize(self, audio_path:str)->list:
        self.result_text = ""
        self.wsParam = Ws_Param(
            APPID=self.APPID,
            APISecret=self.APISecret,
            APIKey=self.APIKey,
            AudioFile=audio_path
        )
        websocket.enableTrace(False)
        wsUrl = self.wsParam.create_url()
        ws = websocket.WebSocketApp(
            wsUrl, on_message=self.on_message, on_error=self.on_error, on_close=self.on_close
        )
        ws.on_open = self.on_open
        ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})

        return [True, self.result_text]
    
    # 收到websocket消息的处理
    def on_message(self, ws, message):
        try:
            code = json.loads(message)["code"]
            sid = json.loads(message)["sid"]
            if code != 0:
                errMsg = json.loads(message)["message"]
                # print("sid:%s call error:%s code is:%s" % (sid, errMsg, code))
            else:
                data = json.loads(message)["data"]["result"]["ws"]

                result = ""
                for i in data:
                    for w in i["cw"]:
                        result += w["w"]

                self.result_text += result

        except Exception as e:
            print("receive msg,but parse exception:", e)

    # 收到websocket连接建立的处理
    def on_open(self, ws):
        def run(*args):
            frameSize = 8000  # 每一帧的音频大小
            intervel = 0.04  # 发送音频间隔(单位:s)
            status = (
                self.STATUS_FIRST_FRAME  # 音频的状态信息，标识音频是第一帧，还是中间帧、最后一帧
            )

            with open(self.wsParam.AudioFile, "rb") as fp:
                while True:
                    buf = fp.read(frameSize)
                    # 文件结束
                    if not buf:
                        status = self.STATUS_LAST_FRAME
                    # 第一帧处理
                    # 发送第一帧音频，带business 参数
                    # appid 必须带上，只需第一帧发送
                    if status == self.STATUS_FIRST_FRAME:

                        d = {
                            "common": self.wsParam.CommonArgs,
                            "business": self.wsParam.BusinessArgs,
                            "data": {
                                "status": 0,
                                "format": "audio/L16;rate=16000",
                                "audio": str(base64.b64encode(buf), "utf-8"),
                                "encoding": "raw",
                            },
                        }
                        d = json.dumps(d)
                        ws.send(d)
                        status = self.STATUS_CONTINUE_FRAME
                    # 中间帧处理
                    elif status == self.STATUS_CONTINUE_FRAME:
                        d = {
                            "data": {
                                "status": 1,
                                "format": "audio/L16;rate=16000",
                                "audio": str(base64.b64encode(buf), "utf-8"),
                                "encoding": "raw",
                            }
                        }
                        try:
                            ws.send(json.dumps(d))
                        except websocket._exceptions.WebSocketConnectionClosedException:
                            # 连接已关闭，退出循环
                            break
                    # 最后一帧处理
                    elif status == self.STATUS_LAST_FRAME:
                        d = {
                            "data": {
                                "status": 2,
                                "format": "audio/L16;rate=16000",
                                "audio": str(base64.b64encode(buf), "utf-8"),
                                "encoding": "raw",
                            }
                        }
                        try:
                            ws.send(json.dumps(d))
                            time.sleep(1)
                        except websocket._exceptions.WebSocketConnectionClosedException:
                            # 连接已关闭，退出循环
                            break
                        break
                    # 模拟音频采样间隔
                    time.sleep(intervel)
            ws.close()

        threading.Thread(target=run).start()
            
    # 收到websocket错误的处理
    def on_error(self, ws, error):
        print("### error:", error)

    # 收到websocket关闭的处理
    def on_close(self, ws, close_status_code, close_msg):
        # print("###speak iat closed ###")
        pass


class Ws_Param(object):
    # 初始化
    def __init__(self, APPID, APIKey, APISecret, AudioFile):

        self.APPID = APPID
        self.APIKey = APIKey
        self.APISecret = APISecret
        self.AudioFile = AudioFile

        # 公共参数(common)
        self.CommonArgs = {"app_id": self.APPID}
        # 业务参数(business)，更多个性化参数可在官网查看
        self.BusinessArgs = {
            "domain": "iat",
            "language": "en_us",
            "accent": "mandarin",
            "vinfo": 1,
            "vad_eos": 10000,
        }

    # 生成url
    def create_url(self):
        url = "wss://ws-api.xfyun.cn/v2/iat"
        # 生成RFC1123格式的时间戳
        now = datetime.now()
        date = format_date_time(mktime(now.timetuple()))

        # 拼接字符串
        signature_origin = "host: " + "ws-api.xfyun.cn" + "\n"
        signature_origin += "date: " + date + "\n"
        signature_origin += "GET " + "/v2/iat " + "HTTP/1.1"
        # 进行hmac-sha256进行加密
        signature_sha = hmac.new(
            self.APISecret.encode("utf-8"),
            signature_origin.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        signature_sha = base64.b64encode(signature_sha).decode(encoding="utf-8")

        authorization_origin = (
            'api_key="%s", algorithm="%s", headers="%s", signature="%s"'
            % (self.APIKey, "hmac-sha256", "host date request-line", signature_sha)
        )
        authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode(
            encoding="utf-8"
        )
        # 将请求的鉴权参数组合为字典
        v = {"authorization": authorization, "date": date, "host": "ws-api.xfyun.cn"}
        # 拼接鉴权参数，生成url
        url = url + "?" + urlencode(v)
        return url

#for test
# def main():
#     asr_client=Tongyi_ASR(model_name='paraformer-realtime-v2',sample_rate=16000)
#     asr_client.init_asr_engine()
#     res=asr_client.recognize("/home/jetson/yahboomM3_ws/user_ws/cache/user_speech.wav")
#     print(res[1])


# if __name__ == "__main__":
#     main()