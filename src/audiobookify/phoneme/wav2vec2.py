import soundfile as sf


def detect_device() -> str:
    import torch
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class Wav2Vec2PhonemeExtractor:
    model_name = "facebook/wav2vec2-lv-60-espeak-cv-ft"
    target_sample_rate = 16000

    def __init__(self):
        self.model = None
        self.processor = None
        self.device = None

    def load(self, device: str = "auto") -> None:
        from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

        self.device = detect_device() if device == "auto" else device
        self.processor = Wav2Vec2Processor.from_pretrained(self.model_name)
        self.model = Wav2Vec2ForCTC.from_pretrained(self.model_name).to(self.device)
        self.model.eval()

    def extract(self, audio_path: str) -> str:
        import torch

        data, sample_rate = sf.read(audio_path, always_2d=True, dtype="float32")
        mono = data.mean(axis=1)

        if sample_rate != self.target_sample_rate:
            import torchaudio

            waveform = torch.from_numpy(mono).unsqueeze(0)
            waveform = torchaudio.functional.resample(waveform, sample_rate, self.target_sample_rate)
            mono = waveform.squeeze(0).numpy()

        inputs = self.processor(mono, sampling_rate=self.target_sample_rate, return_tensors="pt")
        with torch.no_grad():
            logits = self.model(inputs.input_values.to(self.device)).logits
        predicted_ids = torch.argmax(logits, dim=-1)
        return self.processor.batch_decode(predicted_ids)[0]

    def teardown(self) -> None:
        self.model = None
        self.processor = None
