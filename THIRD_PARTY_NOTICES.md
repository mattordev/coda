# Third-party notices

## Pocket TTS 3.0.2

CODA integrates the Pocket TTS Python implementation distributed by Kyutai
under the MIT licence:

https://github.com/kyutai-labs/pocket-tts

Pocket model and voice assets are downloaded at runtime and are not
redistributed with CODA. The gated `kyutai/pocket-tts` voice-cloning weights
and anonymous `kyutai/pocket-tts-without-voice-cloning` weights are published
under CC BY 4.0 with additional prohibited-use conditions recorded on their
model cards:

https://huggingface.co/kyutai/pocket-tts

https://huggingface.co/kyutai/pocket-tts-without-voice-cloning

The default Alba MacKenna voice recordings are published under CC BY 4.0.
Other voices in the upstream repository may use different licences, including
CC0, CC BY 4.0 and CC BY-NC 4.0:

https://huggingface.co/kyutai/tts-voices

## SpeechRecognition 3.17.0

CODA's `utils/phrase_listener.py` adapts the voice-activity-detection loop from
SpeechRecognition 3.17.0.

Copyright (c) 2014-, Anthony Zhang <azhang9@gmail.com>
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.
3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software
   without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
