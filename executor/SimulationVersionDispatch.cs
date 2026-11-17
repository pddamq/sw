using System.Globalization;
using System.Reflection;
using System.Runtime.ExceptionServices;
using System.Runtime.InteropServices;
using SolidWorks.Interop.cosworks;

namespace SolidWorksMcp.Core.Com;

// The public compile-only Simulation metadata is older than three upstream API members.
// Invoke those exact members through COM IDispatch; do not emulate results or substitute
// older API semantics. An unsupported target SW version returns its real COM failure.
internal static class SimulationVersionDispatch
{
    static object? Invoke(object target, string name, object?[] args, params int[] outputs)
    {
        var modifier = new ParameterModifier(args.Length);
        foreach (int index in outputs) modifier[index] = true;
        try
        {
            return target.GetType().InvokeMember(name, BindingFlags.InvokeMethod, null, target, args,
                outputs.Length == 0 ? null : [modifier], CultureInfo.InvariantCulture, null);
        }
        catch (TargetInvocationException ex) when (ex.InnerException is not null)
        {
            ExceptionDispatchInfo.Capture(ex.InnerException).Throw();
            throw;
        }
    }

    public static void SetForceComponentValues2(this CWForce force, bool use1, bool use2, bool use3,
        double value1, double value2, double value3) =>
        Invoke(force, "SetForceComponentValues2", [use1, use2, use3, value1, value2, value3]);

    public static void GetForceComponentValues2(this CWForce force, out bool use1, out bool use2, out bool use3,
        out double value1, out double value2, out double value3)
    {
        object?[] args = [false, false, false, 0.0, 0.0, 0.0];
        Invoke(force, "GetForceComponentValues2", args, 0, 1, 2, 3, 4, 5);
        use1 = Convert.ToBoolean(args[0], CultureInfo.InvariantCulture);
        use2 = Convert.ToBoolean(args[1], CultureInfo.InvariantCulture);
        use3 = Convert.ToBoolean(args[2], CultureInfo.InvariantCulture);
        value1 = Convert.ToDouble(args[3], CultureInfo.InvariantCulture);
        value2 = Convert.ToDouble(args[4], CultureInfo.InvariantCulture);
        value3 = Convert.ToDouble(args[5], CultureInfo.InvariantCulture);
    }

    public static double GetPropertyByName2(this CWMaterial material, int units, string name, out int error)
    {
        object?[] args = [units, name, 0];
        object? value = Invoke(material, "GetPropertyByName2", args, 2);
        error = Convert.ToInt32(args[2], CultureInfo.InvariantCulture);
        return Convert.ToDouble(value, CultureInfo.InvariantCulture);
    }
}
