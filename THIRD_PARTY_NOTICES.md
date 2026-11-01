# Third-party software and models

This application bundles unmodified third-party model weights and their required runtime components. Their original licenses continue to apply; this document does not relicense them.

- IndexTTS-2.5 source and weights: Bilibili Model Use License Agreement. The complete original `LICENSE`, `LICENSE_ZH.txt`, model `LICENSE`, notices and revision are retained under `engines/index/`.
- Wav2Vec2-BERT 2.0, CAMPPlus, BigVGAN and the IndexTTS codec: used by IndexTTS; original included notices and model metadata are retained with their weights and source.
- Faster-Whisper / CTranslate2 and the Whisper small weights: the installed package licenses and model license are retained in their respective distribution/model directories.
- Sherpa-ONNX, GTCRN and UVR: original package licenses and model notices are retained. Audio processing is local and optional.
- CPython, PyTorch, CUDA user-space libraries, NumPy, SoundFile, imageio-ffmpeg and other Python dependencies: original licenses remain in the bundled runtimes, including package `.dist-info` license files. NVIDIA display drivers are not included.
- The original Microsoft Visual C++ Redistributable installer, when included, is provided with its own Microsoft license and signature. Installation can require Windows UAC consent.

Any modifications made to the original model in this Derivative Work are not endorsed, warranted, or guaranteed by the original right-holder of the original model, and the original right-holder disclaims all liability related to this Derivative Work.

No personal reference recordings, generated speech, task histories, credentials, or development download caches are included in offline release artifacts.

Upstream sources:

- https://github.com/index-tts/index-tts
- https://huggingface.co/IndexTeam/IndexTTS-2.5
- https://github.com/SYSTRAN/faster-whisper
- https://github.com/k2-fsa/sherpa-onnx
- https://github.com/python/cpython
- https://pytorch.org/

The offline installer requires acceptance of the bundled third-party license terms before installation. In particular, redistribution and use of IndexTTS are subject to its original model-use restrictions and downstream obligations.
