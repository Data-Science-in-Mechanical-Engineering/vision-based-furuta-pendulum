import functools
import pickle

import matplotlib.pyplot as plt
import numpy as np

from gym_brt.control.control import _convert_state
from gym_brt.quanser import QubeHardware, QubeSimulator
from gym_brt.quanser.qube_interfaces import forward_model_ode


# from simulator_tuning.qube_simulator_optimize import forward_model_ode_optimize


# No input
def zero_policy(state, **kwargs):
    return np.array([0.0])


# Constant input
def constant_policy(state, **kwargs):
    value = 4.0
    return np.array([value])


# Rand input
def random_policy(state, **kwargs):
    return np.asarray([np.random.randn()])


# Square wave, switch every 85 ms
def square_wave_policy(state, step, frequency=250, **kwargs):
    # steps_until_85ms = int(85 * (frequency / 300))
    # state = _convert_state(state)
    # # Switch between positive and negative every 85 ms
    # mod_170ms = step % (2 * steps_until_85ms)
    # if mod_170ms < steps_until_85ms:
    #     action = 3.0
    # else:
    #     action = -3.0
    action = 3.0 * np.sin(step / frequency / 0.1)

    return np.array([action])


# Flip policy
def energy_control_policy(state, **kwargs):
    state = _convert_state(state)
    # Run energy-based control to flip up the pendulum
    theta, alpha, theta_dot, alpha_dot = state
    # alpha_dot += alpha_dot + 1e-15

    """Implements a energy based swing-up controller"""
    mu = 50.0  # in m/s/J
    ref_energy = 30.0 / 1000.0  # Er in joules

    # TODO: Which one is correct?
    max_u = 6  # Max action is 6m/s^2
    # max_u = 0.85  # Max action is 6m/s^2

    # System parameters
    jp = 3.3282e-5
    lp = 0.129
    lr = 0.085
    mp = 0.024
    mr = 0.095
    rm = 8.4
    g = 9.81
    kt = 0.042

    pend_torque = (1 / 2) * mp * g * lp * (1 + np.cos(alpha))
    energy = pend_torque + (jp / 2.0) * alpha_dot * alpha_dot

    u = mu * (energy - ref_energy) * np.sign(-1 * np.cos(alpha) * alpha_dot)
    u = np.clip(u, -max_u, max_u)

    torque = (mr * lr) * u
    voltage = (rm / kt) * torque
    return np.array([-voltage])


# Hold policy
def pd_control_policy(state, **kwargs):
    state = _convert_state(state)
    theta, alpha, theta_dot, alpha_dot = state
    # multiply by proportional and derivative gains
    kp_theta = -2.0
    kp_alpha = 35.0
    kd_theta = -1.5
    kd_alpha = 3.0

    # If pendulum is within 20 degrees of upright, enable balance control, else zero
    if np.abs(alpha) <= (20.0 * np.pi / 180.0):
        action = (
                theta * kp_theta
                + alpha * kp_alpha
                + theta_dot * kd_theta
                + alpha_dot * kd_alpha
        )
    else:
        action = 0.0
    action = np.clip(action, -3.0, 3.0)
    return np.array([action])


# Flip and Hold
def flip_and_hold_policy(state, **kwargs):
    state = _convert_state(state)
    theta, alpha, theta_dot, alpha_dot = state

    # If pendulum is within 20 degrees of upright, enable balance control
    if np.abs(alpha) <= (20.0 * np.pi / 180.0):
        action = pd_control_policy(state)
    else:
        action = energy_control_policy(state)
    return action


# Square wave instead of energy controller flip and hold
def square_wave_flip_and_hold_policy(state, **kwargs):
    state = _convert_state(state)
    theta, alpha, theta_dot, alpha_dot = state

    # If pendulum is within 20 degrees of upright, enable balance control
    if np.abs(alpha) <= (20.0 * np.pi / 180.0):
        action = pd_control_policy(state)
    else:
        action = square_wave_policy(state, **kwargs)
    return action


# Run on the hardware
def run_qube(begin_up, policy, nsteps, frequency, integration_steps):
    with QubeHardware(frequency=frequency, max_voltage=3.0) as qube:

        if begin_up is True:
            s = qube.reset_up()
        elif begin_up is False:
            s = qube.reset_down()

        # Wait for a little bit before reading the initial state
        for _ in range(15):
            a = random_policy(s)
            s = qube.step(a)

        init_state = s
        a = policy(s, step=0)
        s_hist = [s]
        a_hist = [a]

        for i in range(nsteps):
            s = qube.step(a)
            a = policy(s, step=i + 1, frequency=frequency)

            s_hist.append(s)  # States
            a_hist.append(a)  # Actions

        # Return a 2d array, hist[n,d] gives the nth timestep and the dth dimension
        # Dims are ordered as: ['Theta', 'Alpha', 'Theta dot', 'Alpha dot', 'Action']
        return np.concatenate((np.array(s_hist), np.array(a_hist)), axis=1), init_state


def run_mujoco(begin_up, policy, n_steps, frequency, integration_steps, params=None, init_state=None, render=False):
    from gym_brt.envs.simulation.mujoco import QubeMujoco
    with QubeMujoco(
            frequency=frequency,
            integration_steps=integration_steps,
            max_voltage=18.0
    ) as qube:

        def set_init_from_ob(ob):
            pos = ob[:2]
            pos[1] *= -1
            vel = ob[2:]
            vel[1] *= -1

            print(pos)
            print(vel)

            qube.set_state(pos, vel)
            return qube._get_obs()

        if begin_up is True:
            s = qube.reset_up()
        elif begin_up is False:
            s = qube.reset_down()

        if init_state is not None:
            s = set_init_from_ob(init_state)

        if render:
            qube.render()

        # if params is not None:
        # qube.model.dof_damping[:] = params[:]
        # qube.actuation_consts[:] = params[:]

        init_state = s
        a = policy(s, step=0)
        s_hist = [s]
        a_hist = [a]

        for i in range(n_steps):
            if render:
                qube.render()
            s = qube.step(a)
            a = policy(s, step=i + 1, frequency=frequency)

            s_hist.append(s)  # States
            a_hist.append(a)  # Actions

        # Return a 2d array, hist[n,d] gives the nth timestep and the dth dimension
        # Dims are ordered as: ['Theta', 'Alpha', 'Theta dot', 'Alpha dot', 'Action']
        return np.concatenate((np.array(s_hist), np.array(a_hist)), axis=1), init_state


def run_sim(begin_up, policy, nsteps, frequency, integration_steps, params=None, init_state=None, render=False):
    if params is not None:
        sim_function = functools.partial(forward_model_ode_optimize, params=params)
    else:
        sim_function = forward_model_ode

    with QubeSimulator(
            forward_model=sim_function,
            frequency=frequency,
            integration_steps=integration_steps,
            max_voltage=18.0,
    ) as qube:
        if begin_up is True:
            s = qube.reset_up()
        elif begin_up is False:
            s = qube.reset_down()

        if init_state is not None:
            qube.state = np.asarray(init_state, dtype=np.float64)  # Set the initial state of the simulator
            s = init_state

        if render:
            from gym_brt.envs.rendering import QubeRenderer
            viewer = QubeRenderer(s[0], s[1], frequency)
            viewer.render(s[0], s[1])

        a = policy(s, step=0)
        s_hist = [s]
        a_hist = [a]

        for i in range(nsteps):
            if render and viewer is not None:
                viewer.render(s[0], s[1])
            s = qube.step(a)
            a = policy(s, step=i + 1, frequency=frequency)

            s_hist.append(s)  # States
            a_hist.append(a)  # Actions

        # Return a 2d array, hist[n,d] gives the nth timestep and the dth dimension
        # Dims are ordered as: ['Theta', 'Alpha', 'Theta dot', 'Alpha dot', 'Action']
        return np.concatenate((np.array(s_hist), np.array(a_hist)), axis=1)


def plot_results(hists, labels, colors=None, normalize=None):
    state_dims = ['Theta', 'Alpha', 'Theta dot', 'Alpha dot', 'Action']

    f, (ax1, ax2, ax3, ax4, ax5) = plt.subplots(5, 1, sharex=True)
    for i, ax in enumerate((ax1, ax2, ax3, ax4, ax5)):

        # Normalize to be between 0 and 2pi
        if normalize is not None:
            if state_dims[i].lower() in normalize:
                for hist in hists:
                    hist[:, i] %= 2 * np.pi

        # Plot with specific colors
        if colors is not None:
            for hist, label, color in zip(hists, labels, colors):
                ax.plot(hist[:, i], label=label, color=color)

        # Default colors
        else:
            for hist, label in zip(hists, labels):
                ax.plot(hist[:, i], label=label)
        ax.set_ylabel(state_dims[i])
        ax.legend()
    plt.show()


def save(hist_path, hist_data, init_state_path, init_state):
    outfile = open(hist_path, "wb")
    pickle.dump(hist_data, outfile)
    outfile = open(init_state_path, "wb")
    pickle.dump(init_state, outfile)


def load(hist_path, init_state_path):
    infile = open(hist_path, "rb")
    hist = pickle.load(infile)
    infile = open(init_state_path, "rb")
    init_state = pickle.load(infile)
    return hist, init_state


def run_real():
    # Natural response when starting at α = 0 + noise (upright/inverted)
    hist_qube, init_state = run_qube(BEGIN_UP, POLICY, N_STEPS, FREQUENCY, I_STEPS)

    save("data/backup/hist_qube_real", hist_qube, "data/backup/init_state_real", init_state)
    return hist_qube, init_state


def run_muj(params=None, init=None, render=False):
    # Natural response when starting at α = 0 + noise (upright/inverted)
    if init is not None:
        init_state = np.copy(init)
    else:
        init_state = None

    hist_muj, init_state = run_mujoco(BEGIN_UP, POLICY, N_STEPS, FREQUENCY, I_STEPS, params=params,
                                      init_state=init_state, render=render)

    save("../simulator_tuning/data/hist_qube_muj", hist_muj, "../simulator_tuning/data/init_state_muj", init_state)
    return hist_muj, init_state


def run_ode(params=None, init=None, render=False):
    if init is not None:
        init_state = np.copy(init)
    else:
        init_state = None

    hist_ode = run_sim(BEGIN_UP, POLICY, N_STEPS, FREQUENCY, I_STEPS, params=params, init_state=init_state,
                       render=render)

    save("../simulator_tuning/data/hist_qube_ode", hist_ode, "../simulator_tuning/data/init_state_ode", init_state)
    return hist_ode, init_state


def parameter_search():
    from ray import tune
    from gym_brt.envs.simulation.mujoco import QubeMujoco
    from copy import deepcopy
    from simmod.modification.mujoco import MujocoBodyModifier, MujocoJointModifier, MujocoActuatorModifier

    real, INIT_STATE = load("data/backup/hist_qube_real", "data/backup/init_state_real")

    def evaluation_function(config, provided_actions=True):
        frequency = FREQUENCY
        integration_steps = I_STEPS
        begin_up = BEGIN_UP
        policy = POLICY
        n_steps = N_STEPS
        init_state = deepcopy(INIT_STATE)
        actions = real[:, -1]

        with QubeMujoco(frequency=frequency, integration_steps=integration_steps, max_voltage=18.0) as qube:
            qube.Rm = config["Rm"] if "Rm" in config else qube.Rm
            qube.kt = config["kt"] if "kt" in config else qube.kt
            qube.km = config["km"] if "km" in config else qube.km
            body_mod = MujocoBodyModifier(qube.sim)
            jnt_mod = MujocoJointModifier(qube.sim)
            if "mass_arm" in config:
                body_mod.set_mass("arm", config["mass_arm"])
            if "mass_pole" in config:
                body_mod.set_mass("pole", config["mass_pole"])
            if "damping_arm_pole" in config:
                jnt_mod.set_damping("arm_pole", config["damping_arm_pole"])
            if "damping_base_motor" in config:
                jnt_mod.set_damping("base_motor", config["damping_base_motor"])
            if "gear_motor_rotation" in config:
                act_mod = MujocoActuatorModifier(qube.sim)
                act_mod.set_gear("motor_rotation", config["gear_motor_rotation"])

            def set_init_from_ob(ob):
                pos = ob[:2]
                pos[1] *= -1
                vel = ob[2:]
                vel[1] *= -1

                qube.set_state(pos, vel)
                return qube._get_obs()

            if begin_up is True:
                s = qube.reset_up()
            elif begin_up is False:
                s = qube.reset_down()

            if init_state is not None:
                s = set_init_from_ob(init_state)

            a = actions[0] if provided_actions else policy(s, step=0)
            s_hist = [s]
            a_hist = [a]

            for i in range(n_steps):
                s = qube.step(a)
                a = actions[i+1] if provided_actions else policy(s, step=i + 1, frequency=frequency)

                s_hist.append(s)  # States
                a_hist.append(a)  # Actions

            # Return a 2d array, hist[n,d] gives the nth timestep and the dth dimension
            # Dims are ordered as: ['Theta', 'Alpha', 'Theta dot', 'Alpha dot', 'Action']
            pred = np.concatenate((np.array(s_hist), np.array(a_hist)), axis=1)

        score = np.sqrt(np.mean((pred[:, :-1] - real[:, :-1]) ** 2))
        tune.report(score=score)

    # TODO: Set configuration
    configuration = {
        "damping_arm_pole": tune.grid_search(np.arange(1e-06, 1e-04, 1e-06).tolist()),
        "damping_base_motor": tune.grid_search(np.arange(1e-06, 1e-04, 1e-06).tolist()),
        "gear_motor_rotation": tune.grid_search(np.arange(0.5, 1.5, 0.1).tolist()),
        "mass_arm": tune.grid_search(np.arange(0.006, 0.007, 0.0001).tolist()),
        "mass_motor": tune.grid_search(np.arange(0.088, 0.09, 0.0001).tolist()),
        "Rm": tune.grid_search(np.arange(8., 9.1, 0.1).tolist()),
        "kt": tune.grid_search(np.arange(0.035, 0.048, 0.001).tolist()),
        "km": tune.grid_search(np.arange(0.035, 0.048, 0.001).tolist()),
    }

    analysis = tune.run(evaluation_function, config=configuration)

    # Get a dataframe for analyzing trial results.
    recommendation = analysis.get_best_config(metric="score")
    print("Best config: ", recommendation)

    return recommendation


def visualize(plot_real_qube=False, plot_mujoco=False, plot_ode=False):
    assert plot_real_qube or plot_mujoco or plot_ode, "At least one mode (hardware or simulation) must be plotted"

    hists = list()
    labels = list()

    if plot_real_qube:
        hist_qube, _ = load("data/backup/hist_qube_real", "data/backup/init_state_real")
        hists.append(hist_qube)
        labels.append("Hardware")

    if plot_mujoco:
        hist_muj, _ = load("../simulator_tuning/data/hist_qube_muj", "../simulator_tuning/data/init_state_muj")
        hists.append(hist_muj)
        labels.append("Mujoco")

    if plot_ode:
        hist_ode, _ = load("../simulator_tuning/data/hist_qube_ode", "../simulator_tuning/data/init_state_muj")
        hists.append(hist_ode)
        labels.append("ODE")

    # if plot_mujoco and plot_real_qube:
    # res = np.sqrt(np.mean((hist_qube[:, :-1] - hist_muj[:, :-1]) ** 2))
    # print(res)

    plot_results(hists=hists, labels=labels, normalize=['alpha'])


def record_traj(n_steps, frequency=250):
    from gym_brt.control import QubeFlipUpControl
    from gym_brt.envs import QubeSwingupEnv

    solved = False
    with QubeSwingupEnv(use_simulator=False, frequency=frequency) as env:
        controller = QubeFlipUpControl(sample_freq=frequency, env=env)
        while not solved:
            trajectory = list()
            actions = list()
            state = init_state = env.reset()
            for step in range(n_steps):
                action = controller.action(state)
                #trajectory.append(tuple(state, action))
                trajectory.append(action)
                actions.append(action)
                state, reward, done, info = env.step(action)
                _, alpha, _, _ = state
                if np.abs(alpha) <= (20.0 * np.pi / 180.0):
                    solved = True
                if done:
                    print(solved)
                    break
    return np.concatenate((np.array(trajectory), np.array(actions))), init_state


if __name__ == '__main__':
    # Constants between experiments
    FREQUENCY = 100
    run_time = 10  # in seconds
    N_STEPS = int(run_time * FREQUENCY)
    I_STEPS = 1  # Iteration steps
    plt.rcParams["figure.figsize"] = (20, 20)  # make graphs BIG
    POLICY = zero_policy
    BEGIN_UP = True

    PARAMETER_SEARCH = True
    TRAJ_RECODRING = False

    if PARAMETER_SEARCH:
        rec = parameter_search()
        print(f"Last recommendation: {rec}")
    elif TRAJ_RECODRING:
        hist, init_state = record_traj(n_steps=N_STEPS, frequency=FREQUENCY)
        save("data/hist_qube_real", hist,
             "data/init_state_real", init_state)
        print('Finished')
    else:
        # Choose which mode should be run
        RUN_REAL = False
        RUN_MUJ = True
        RUN_ODE = True

        RENDER_MUJ = True
        RENDER_ODE = False

        if RENDER_ODE:
            assert RUN_ODE, "ODE simulation cannot be rendered if it will not be executed"
        if RENDER_MUJ:
            assert RUN_MUJ, "Mujoco simulation cannot be rendered if it will not be executed"

        assert RUN_ODE or RUN_REAL or RUN_MUJ, "At least one mode (real/simulation) must be executed"

        # # evaluate
        if RUN_REAL:
            run_real()
        _, init = load("data/backup/hist_qube_real", "data/backup/init_state_real")
        # _, init = load("../simulator_tuning/data/hist_qube_real_swingup", "../simulator_tuning/data/init_state_real_swingup")
        # init = np.array([0, 0, 0, 0]) + np.random.uniform(size=4, low=-0.01, high=0.01)
        print(f"Initialisation state for the simulations: \n {init}")
        if RUN_MUJ:
            run_muj(init=init, render=RENDER_MUJ)
        if RUN_ODE:
            run_ode(init=init, render=RENDER_ODE)
        visualize(plot_real_qube=True, plot_mujoco=RUN_MUJ, plot_ode=RUN_ODE)
