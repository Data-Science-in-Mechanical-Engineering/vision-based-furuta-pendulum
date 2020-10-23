"""
Calibration methods for the real qube

Adapted from:
.. https://git.ias.informatik.tu-darmstadt.de/watson/clients/-/blob/master/quanser_robots/qube/qube_rr.py
PD was changed to a PID.

@Author: Moritz Schneider
"""
import numpy as np
import time
import warnings
import typing as tp

# For other platforms where it's impossible to install the HIL SDK
try:
    from gym_brt.quanser import QubeHardware
except ImportError:
    print("Warning: Can not import QubeHardware in calibration.py. Calibration not possible!")

import math


class PIDCtrl:
    """
    Slightly tweaked PID controller (increases gains if `x_des` not reachable).

    Accepts `th_des` and drives Qube to `x_des = (th_des, 0.0, 0.0, 0.0)`

    Flag `done` is set when `|x_des - x| < tol`.

    Tweak: increase P-gain on `th` if velocity is zero but the goal is still
    not reached (useful for counteracting resistance from the power cord).
    """

    def __init__(self, fs_ctrl, K=None, th_des=0.0, tol=1e-3):
        self.done = False
        self.K = K if K is not None else [2.5, 0.0, 0.5, 0.0]
        self.th_des = th_des
        self.tol = tol
        self._dt = 1./fs_ctrl
        self.integrated_err = 0.0

    def __call__(self, x):
        th, al, thd, ald = x
        K, th_des, tol = self.K, self.th_des, self.tol
        all_but_th_squared = al ** 2 + thd ** 2 + ald ** 2
        self.integrated_err += (th_des - th) * self._dt
        err = np.sqrt((th_des - th) ** 2)
        if not self.done and err < tol:
            self.done = True
        #elif th_des and np.sqrt(all_but_th_squared) < tol / 5.0:
        #    # Increase P-gain on `th` when struggling to reach `th_des`
        #    K[0] += 0.1 * K[0]
        return -1*np.array([K[0]*(th_des - th) + K[0]*self.integrated_err - K[1]*al - K[2]*thd - K[3]*ald])


class GoToLimCtrl:
    """Go to joint limits by applying `u_max`; save limit value in `th_lim`."""

    def __init__(self, fs_ctrl, positive=True, u_max=1.0):
        self.done = False
        self.th_lim = 10.0
        self.sign = 1 if positive else -1
        self.u_max = u_max
        self.cnt = 0
        self.cnt_done = int(0.3*fs_ctrl)

    def __call__(self, x):
        th = x[0]
        if np.abs(th - self.th_lim) > 0:
            self.cnt = 0
            self.th_lim = th
        else:
            self.cnt += 1
        self.done = self.cnt == self.cnt_done
        return -1*np.array([self.sign * self.u_max])


class CalibrCtrl:
    """Go to joint limits, find midpoint, go to the midpoint."""

    def __init__(self, fs_ctrl, u_max=1.0, th_des=0.0, limits=None):
        self.done = False
        self.go_right = GoToLimCtrl(fs_ctrl, positive=True, u_max=u_max)
        self.go_left = GoToLimCtrl(fs_ctrl, positive=False, u_max=u_max)
        self.limits = limits
        self.go_desired = PIDCtrl(fs_ctrl=fs_ctrl, K=[2.5, 0.0, 1.0, 0.0], th_des=th_des)
        self.time = 0.
        self.time_lim = 10.
        self.set_desired = False

    def __call__(self, x):
        u = np.array([0.0])
        if not self.go_right.done and self.limits is None:
            u = self.go_right(x)
        elif not self.go_left.done and self.limits is None:
            u = self.go_left(x)
        elif not self.go_desired.done:
            if not self.set_desired:
                if self.limits is None:
                    self.limits = (self.go_left.th_lim, self.go_right.th_lim)
                self.time = time.time()
                self.go_desired.th_des += sum(self.limits) / 2
                self.set_desired = True
            if time.time() - self.time > self.time_lim:
                warnings.warn("Timed out setting desired theta. Continue with current setting.")
                self.go_desired.done = True
            u = self.go_desired(x)
        elif not self.done:
            self.done = True
        return u


def calibrate(qube=None, desired_theta: float = 0.0, frequency: int = 120, u_max: float = 1.0, unit: str = 'deg',
              limits: tp.Tuple = None):
    if unit == 'deg':
        desired_theta = (math.pi/180.) * desired_theta
    elif unit == 'rad':
        desired_theta = desired_theta
    else:
        raise ValueError(f"Unknown angle unit '{unit}'")

    if qube is None:
        with QubeHardware(frequency=frequency) as qube:
            return calibrate_qube(qube=qube, desired_theta=desired_theta, frequency=frequency, u_max=u_max, limits=limits)
    else:
        return calibrate_qube(qube=qube, desired_theta=desired_theta, frequency=frequency, u_max=u_max, limits=limits)


def calibrate_qube(qube, desired_theta: float = 0.0, frequency: int = 120, u_max: float = 1.0, limits: tp.Tuple = None)\
        -> tp.Tuple:
    """Calibration of the Quanser Qube-Servo 2 to a given angle for theta.

    Args:
        qube: The Quanser Qube-Servo 2 environment
        desired_theta: Desired angle of theta in rad
        frequency: Frequency during calibration
        u_max: Maximal action to apply during calibration
        unit: Angle unit of theta
        limits: If the limits are know beforehand they can be passed as a tuple in form (limit left, limit right)

    Returns:
        None

    """
    controller = CalibrCtrl(fs_ctrl=frequency, u_max=u_max, th_des=desired_theta, limits=limits)
    qube.reset_down()
    state = qube.state
    while not controller.done:
        action = controller(state)
        state = qube.step(action)

    if limits is None:
        limits = controller.limits

    return limits
