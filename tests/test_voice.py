import numpy as np

from agent_remote.voice import to_16k


def test_48k_is_averaged_down():
    out = to_16k(np.ones(48000, dtype=np.float32), 48000)
    assert out.shape == (16000,)
    assert np.allclose(out, 1.0)


def test_44k1_is_resampled():
    out = to_16k(np.zeros(44100, dtype=np.float32), 44100)
    assert out.shape == (16000,)


def test_16k_passes_through():
    audio = np.arange(10, dtype=np.float32)
    assert np.array_equal(to_16k(audio, 16000), audio)
