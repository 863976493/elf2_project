class SimplePID:
    """Discrete PID controller with output clamping and integral windup protection."""

    def __init__(self, kp=0.1, ki=0.0, kd=0.1,
                 output_min=None, output_max=None, integral_max=None):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.output_min = output_min
        self.output_max = output_max
        self.integral_max = integral_max
        self.reset()

    def compute(self, target, current):
        """Compute PID output. Returns: control signal."""
        error = target - current
        self.integral += error

        # Integral windup protection
        if self.integral_max is not None:
            self.integral = max(-self.integral_max,
                                min(self.integral_max, self.integral))

        derivative = error - self.prev_error
        output = self.kp * error + self.ki * self.integral + self.kd * derivative
        self.prev_error = error

        # Output clamping
        if self.output_min is not None and output < self.output_min:
            output = self.output_min
        if self.output_max is not None and output > self.output_max:
            output = self.output_max

        return output

    def reset(self):
        """Reset controller state."""
        self.integral = 0.0
        self.prev_error = 0.0
