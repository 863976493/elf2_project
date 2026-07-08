#!/usr/bin/env python3
import argparse
import os
import queue
import subprocess
import threading
import time
import wave

import pygame
import rclpy
import yaml
from ament_index_python.packages import get_package_share_directory
from piper.voice import PiperVoice
from rclpy.node import Node
from std_msgs.msg import String


class CloudVoiceTtsNode(Node):
    def __init__(self, model_path: str, config_path: str, output_path: str, audio_device: str):
        super().__init__("cloud_voice_tts_node")
        self.output_path = output_path
        self.audio_device = audio_device
        self.text_queue: queue.Queue[str] = queue.Queue(maxsize=10)
        self.running = True

        self.subscription = self.create_subscription(
            String,
            "/cloud_voice_text",
            self.voice_text_callback,
            10,
        )

        self.voice = PiperVoice.load(model_path, config_path=config_path, use_cuda=False)
        pygame.mixer.init()

        self.worker = threading.Thread(target=self.worker_loop, daemon=True)
        self.worker.start()
        self.get_logger().info(
            f"cloud voice tts node started, model={model_path}, audio_device={self.audio_device or 'pygame-default'}"
        )

    def voice_text_callback(self, msg: String):
        text = str(msg.data or "").strip()
        if not text:
            return
        try:
            self.text_queue.put_nowait(text)
        except queue.Full:
            self.get_logger().warning("voice queue is full, dropping oldest text")
            try:
                self.text_queue.get_nowait()
            except queue.Empty:
                pass
            self.text_queue.put_nowait(text)

    def worker_loop(self):
        while self.running and rclpy.ok():
            try:
                text = self.text_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            try:
                self.get_logger().info(f"synthesizing voice text: {text}")
                self.synthesize(text)
                self.play_audio(self.output_path)
            except Exception as exc:
                self.get_logger().error(f"voice playback failed: {exc}")

    def synthesize(self, text: str):
        with wave.open(self.output_path, "wb") as wav_file:
            self.voice.synthesize_wav(text, wav_file)

    def play_audio(self, file_path: str):
        if self.audio_device:
            subprocess.run(["aplay", "-q", "-D", self.audio_device, file_path], check=True)
            return
        pygame.mixer.music.load(file_path)
        pygame.mixer.music.play()
        while self.running and pygame.mixer.music.get_busy():
            time.sleep(0.05)

    def destroy_node(self):
        self.running = False
        try:
            pygame.mixer.music.stop()
            pygame.mixer.quit()
        except Exception:
            pass
        super().destroy_node()


def load_tts_paths(language: str, config_file: str, model_path: str, model_config: str):
    if model_path and model_config:
        return model_path, model_config

    if not config_file:
        pkg_path = get_package_share_directory("largemodel")
        config_file = os.path.join(pkg_path, "config", "large_model_interface.yaml")

    with open(config_file, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}

    if language == "en":
        return (
            model_path or config.get("en_tts_model"),
            model_config or config.get("en_tts_json"),
        )
    return (
        model_path or config.get("zh_tts_model"),
        model_config or config.get("zh_tts_json"),
    )


def default_output_path() -> str:
    try:
        pkg_path = get_package_share_directory("largemodel")
        return os.path.join(pkg_path, "resources_file", "cloud_voice_tts.wav")
    except Exception:
        return "/tmp/cloud_voice_tts.wav"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--language", default="zh", choices=["zh", "en"])
    parser.add_argument("--config-file", default="")
    parser.add_argument("--model-path", default="")
    parser.add_argument("--model-config", default="")
    parser.add_argument("--output", default="")
    parser.add_argument(
        "--audio-device",
        default="",
        help="ALSA device for aplay, for example plughw:1,0. Empty uses pygame default.",
    )
    args = parser.parse_args()

    model_path, model_config = load_tts_paths(
        args.language,
        args.config_file,
        args.model_path,
        args.model_config,
    )
    if not model_path or not model_config:
        raise SystemExit("missing Piper model path or model config path")

    output_path = args.output or default_output_path()
    rclpy.init()
    node = CloudVoiceTtsNode(model_path, model_config, output_path, args.audio_device)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
