# Steering Model And Angles

The four-element ideal array model is:

```text
             +y / display 0 deg

        ch1 / ant2        ch2 / ant3


        ch0 / ant1        ch3 / ant4

             +x / display 90 deg
```

Coordinate meaning:

```text
ch0 = bottom-left
ch1 = top-left
ch2 = top-right
ch3 = bottom-right

+x = ch0 -> ch3
+y = ch0 -> ch1
```

The repo uses two angle systems:

```text
display_bearing = (90 - internal_angle) % 360
internal_angle = (90 - display_bearing) % 360
```

Examples:

```text
display 170 = internal 280
display 290 = internal 160
```

`steering_vector()` in `src/antijamming/dsp/doa/music.py` uses internal angles
for MUSIC/Bartlett and calculated response scans. The operator sees display
bearings. The LCMV null constraint uses measured U1 directly, not an angle.
Logs retain `music_internal_angle_deg`, `music_display_bearing_deg`, and
`model_comparison_angle_internal_deg` / `model_comparison_angle_display_deg`.
Retired null-angle and angle-used-to-create-null fields are removed rather
than filled with a MUSIC bearing or left permanently empty.
