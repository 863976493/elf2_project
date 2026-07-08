from abc import ABC, abstractmethod
from piper import PiperVoice,SynthesisConfig
import wave
import os
from urllib.request import urlopen
from urllib.request import Request
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.parse import quote_plus
import json
import yaml
from dashscope.audio.tts_v2 import *
import datetime
import base64
import base64
import hmac
from wsgiref.handlers import format_date_time
from datetime import datetime
from time import mktime
import hashlib
import threading
from cryptography.fernet import Fernet
import websocket
import ssl
import dashscope
class TTS(ABC):
    '''文本转语音抽象基类 / Text-to-Speech Abstract Base Class'''
    @abstractmethod
    def synthesize(self, text: str, audio_path: str):
        """将文本合成为语音"""
        pass
    @abstractmethod
    def init_tts_engine(self):
        """初始化TTS引擎"""
        pass


class PiperTTS(TTS):
    '''Piper TTS 本地文本转语音类 / Piper TTS LocalText-to-Speech Class'''
    def __init__(self,language:str):
        self.language = language
        self.synthesizer = None
        self.syn_config = None

    def init_tts_engine(self):
        if self.language == 'zh':
            tts_model =  os.path.join(os.path.expanduser('~'),"MODELS","tts","zh", "zh_CN-huayan-medium.onnx")
            tts_json =   os.path.join(os.path.expanduser('~'),"MODELS","tts","zh", "zh_CN-huayan-medium.onnx.json")
        elif self.language == 'en':
            tts_model =  os.path.join(os.path.expanduser('~'),"MODELS","tts","en", "en_US-libritts-high.onnx")
            tts_json =   os.path.join(os.path.expanduser('~'),"MODELS","tts","en", "en_US-libritts-high.onnx.json")

        self.syn_config = SynthesisConfig(
            volume=2.0,  # 音量
            length_scale=3.0,  # 语速
            noise_scale=1.0,  # more audio variation
            noise_w_scale=1.0,  # more speaking variation
            normalize_audio=False, # use raw audio from voice
        )
        try:
            self.synthesizer = PiperVoice.load(tts_model,tts_json)
        except Exception as e:
            print(f"Failed to initialize Piper TTS engine: {e}")
            return False
        return True

    def synthesize(self, text: str, audio_path: str):
        try:
            with wave.open(audio_path, "wb") as wav_file:
                self.synthesizer.synthesize_wav(text, wav_file)
        except Exception as e:
            print(f"PiperTTS Failed to synthesize text: {e}")
            return False
        return True



class BaiduTTS(TTS):
    '''百度语音合成 Baidu TTS'''
    def __init__(self,config_file:str):
        self.config_file=config_file
        self.token = None
    def fetch_token(self):
        """
        专用于百度语音合成的token生成方法,百度平台专有的token生成工具
        """
        TOKEN_URL = "http://aip.baidubce.com/oauth/2.0/token"
        SCOPE = "audio_tts_post"  # 有此scope表示有tts能力，没有请在网页里勾选
        params = {
            "grant_type": "client_credentials",
            "client_id": self.api_key,
            "client_secret": self.secret_key,
        }
        post_data = urlencode(params)
        post_data = post_data.encode("utf-8")
        req = Request(TOKEN_URL, post_data)
        try:
            f = urlopen(req, timeout=5)
            result_str = f.read()
        except URLError as err:
            print("token http response http code : " + str(err.code))
            result_str = err.read()

        result_str = result_str.decode()
        result = json.loads(result_str)
        if "access_token" in result.keys() and "scope" in result.keys():
            return result["access_token"]
    def init_tts_engine(self):
        """初始化TTS引擎"""
        try:
            with open(self.config_file, "r") as file:
                    config_param = yaml.safe_load(file)
            self.language=config_param.get("LANGUAGE","zh")
            self.CUID = config_param.get("CUID")
            self.PER = config_param.get("PER")
            self.SPD = config_param.get("SPD")
            self.PIT = config_param.get("PIT")
            self.VOL = config_param.get("VOL")
            self.api_key = config_param.get("BAIDU_API_KEY")
            self.secret_key = config_param.get("BAIDU_SECRET_KEY")
            self.token = self.fetch_token()

        except Exception as e:
            print(f"Failed to initialize Baidu TTS engine: {e}")
            return False
        return True

    def synthesize(self, text: str, audio_path: str):
        TTS_URL = "http://tsn.baidu.com/text2audio"
        tex = quote_plus(text)
        params = {
            "tok": self.token,
            "tex": tex,
            "per": self.PER,
            "spd": self.SPD,
            "pit": self.PIT,
            "vol": self.VOL,
            "aue": 6,
            "cuid": self.CUID,
            "lan": "zh",
            "ctp": 1,
        }  # lan ctp 固定参数

        data = urlencode(params)
        req = Request(TTS_URL, data.encode("utf-8"))

        try:
            f = urlopen(req)
            result_str = f.read()
        except URLError as err:
            print("asr http response http code : " + str(err.code))
            result_str = err.read()
            return False
        with open(audio_path, "wb") as of:
            of.write(result_str)
            return True


class TongyiTTS(TTS):

    '''通义千问语音合成 / Tongyi Qianwen Speech Synthesis'''
    def __init__(self, config_file:str):
        self.config_file=config_file
    def init_tts_engine(self):
        try:
            with open(self.config_file, "r") as file:
                    config_param = yaml.safe_load(file)
            self.oline_tts_model = config_param.get("TONGYI_TTS_MODEL")
            self.voice_tone = config_param.get("VOICE_TONE")
            API=config_param.get("ALIYUN_API_KEY")
            dashscope.api_key = API
        except Exception as e:
            print(f"Failed to initialize Tongyi TTS engine: {e}")
            return False
        return True

    def synthesize(self, text: str, audio_path: str):
        self.synthesizer = SpeechSynthesizer(model=self.oline_tts_model, voice=self.voice_tone, format=AudioFormat.WAV_16000HZ_MONO_16BIT,volume=100)
        audio = self.synthesizer.call(text)
        if audio is None:
            return False
        else:
            with open(audio_path, "wb") as f:
                f.write(audio)
            return True

class XunfeiTTS(TTS):
    '''
    科大讯飞语音合成 / iFLYTEK Speech Synthesis
    '''
    def __init__(self,key_path:str,encrypted_path:str,config_file:str):
        self.config_file=config_file
        self.key_path = key_path
        self.encrypted_path = encrypted_path
        self.wsParam = None
        self.tts_out_path=None
    def init_tts_engine(self):
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

    def synthesize(self, text: str, audio_path: str):
        self.tts_out_path=audio_path
        try:
            self.wsParam = Ws_Param(
                APPID=self.APPID,
                APISecret=self.APISecret,
                APIKey=self.APIKey,
                Text=text,
            )
            websocket.enableTrace(False)
            wsUrl = self.wsParam.create_url()
            ws = websocket.WebSocketApp(
                wsUrl, on_message=self.on_message, on_error=self.on_error, on_close=self.on_close
            )
            # 传递audio_path给on_open回调函数
            ws.on_open = lambda ws: self.on_open(ws, audio_path)
            ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})
        except Exception as e:
            print(f"Failed to initialize Xunfei TTS engine: {e}")
            return False
        return True
    # 收到websocket错误的处理 Handling of websocket errors received
    def on_error(self, ws, error):
        print("XunFei TTS error:", error)

    def on_close(self, ws, close_status_code, close_msg):
        return

    def on_open(self, ws, audio_path):
        def run(*args):
            d = {
                "common": self.wsParam.CommonArgs,
                "business": self.wsParam.BusinessArgs,
                "data": self.wsParam.Data,
            }
            d = json.dumps(d)
            ws.send(d)
            if os.path.exists(audio_path):
                os.remove(audio_path)

        threading.Thread(target=run).start()

    def on_message(self, ws, message):
        try:
            message = json.loads(message)
            code = message["code"]
            sid = message["sid"]
            audio = message["data"]["audio"]
            audio = base64.b64decode(audio)
            status = message["data"]["status"]
            # print(message)
            if status == 2:
                # print("ws is closed")
                ws.close()
            if code != 0:
                errMsg = message["message"]
                print("sid:%s call error:%s code is:%s" % (sid, errMsg, code))
            else:
                with open(self.tts_out_path, "ab") as f:
                    f.write(audio)
        except Exception as e:
            print("receive msg,but parse exception:", e)

class Ws_Param(object):
    # 初始化 initialization
    def __init__(self, APPID, APIKey, APISecret, Text):
        self.APPID = APPID
        self.APIKey = APIKey
        self.APISecret = APISecret
        self.Text = Text

        # 公共参数(common)
        self.CommonArgs = {"app_id": self.APPID}
        # 业务参数(business)，更多个性化参数可在官网查看
        self.BusinessArgs = {
            "aue": "lame",
            "sfl": 1,
            "auf": "audio/L16;rate=16000",
            "vcn": "x4_xiaoyan",
            "tte": "utf8",
            "speed": 50,
            "pitch": 50,
        }
        self.Data = {
            "status": 2,
            "text": str(base64.b64encode(self.Text.encode("utf-8")), "UTF8"),
        }
    # 生成url Generate URL
    def create_url(self):
        url = "wss://tts-api.xfyun.cn/v2/tts"
        # 生成RFC1123格式的时间戳 Generate timestamp in RFC1123 format
        now = datetime.now()
        date = format_date_time(mktime(now.timetuple()))

        # 拼接字符串 Splicing strings
        signature_origin = "host: " + "ws-api.xfyun.cn" + "\n"
        signature_origin += "date: " + date + "\n"
        signature_origin += "GET " + "/v2/tts " + "HTTP/1.1"
        # 进行hmac-sha256进行加密 Encrypt hmac-sha256
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
        # 将请求的鉴权参数组合为字典 Combine the requested authentication parameters into a dictionary
        v = {"authorization": authorization, "date": date, "host": "ws-api.xfyun.cn"}
        # 拼接鉴权参数，生成url Splicing authentication parameters and generating URLs
        url = url + "?" + urlencode(v)
        return url