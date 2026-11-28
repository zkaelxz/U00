package app.baihe.shell;

/**
 * Decides when the phone should say the PC has stopped answering: a whole hour of failed checks on the
 * network of the last good one, announced once per outage, and once more when it answers again. A
 * different network resets the count, since being away from home is not an outage.
 */
final class OutageWatch {
    static final long OUTAGE_AFTER_MS = 60 * 60_000L;

    enum Action { NONE, DOWN, BACK }

    /** What is remembered between checks. */
    static final class State {
        long failingSince;
        String network;
        boolean announced;
    }

    private OutageWatch() {}

    /** One check's result: `answered` is true when the PC replied at all (even with an error status). */
    static Action next(State state, boolean answered, String network, long now) {
        if (answered) {
            boolean wasDown = state.announced;
            state.failingSince = 0;
            state.announced = false;
            state.network = network;
            return wasDown ? Action.BACK : Action.NONE;
        }
        if (network == null || !network.equals(state.network)) {
            // Offline, or not the network the last good check came from.
            state.failingSince = 0;
            return Action.NONE;
        }
        if (state.failingSince == 0) state.failingSince = now;
        if (!state.announced && now - state.failingSince >= OUTAGE_AFTER_MS) {
            state.announced = true;
            return Action.DOWN;
        }
        return Action.NONE;
    }
}
