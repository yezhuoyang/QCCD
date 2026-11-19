"""Basis-aware reset/measurement noise in the standalone order checker."""
import stim

from tools import order


def test_x_basis_reset_and_measurement_noise():
    source = stim.Circuit("RX 0\nR 1\nH 1\nCX 1 0\nMX 0\nMRX 1")
    head, tail, pairs = order.split_source(source)
    noisy = order.build(head, tail, pairs, True)
    assert noisy == stim.Circuit("""
        RX 0
        DEPOLARIZE1(0.001) 0
        R 1
        DEPOLARIZE1(0.001) 1
        H 1
        DEPOLARIZE1(0.001) 1
        CX 1 0
        DEPOLARIZE2(0.001) 1 0
        Z_ERROR(0.001) 0
        MX 0
        Z_ERROR(0.001) 1
        MRX 1
    """)
    assert order.build(head, tail, pairs, False) == source


def test_z_basis_noise_is_unchanged():
    source = stim.Circuit("R 0 1\nH 1\nCX 1 0\nH 1\nMR 1\nM 0")
    head, tail, pairs = order.split_source(source)
    assert order.build(head, tail, pairs, True) == stim.Circuit("""
        R 0 1
        DEPOLARIZE1(0.001) 0 1
        H 1
        DEPOLARIZE1(0.001) 1
        CX 1 0
        DEPOLARIZE2(0.001) 1 0
        H 1
        DEPOLARIZE1(0.001) 1
        X_ERROR(0.001) 1
        MR 1
        X_ERROR(0.001) 0
        M 0
    """)
