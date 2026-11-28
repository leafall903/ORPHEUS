use super::*;

const TARGET: u32 = 13;
const BONE: u64 = 0x0880_0000_0000_00ab;
const LOCAL: [f64; 3] = [1.0, 2.0, 3.0];

fn query(bone: Json) -> Json {
    json!({
        "$":"STU_C5148EFE",
        "m_entity":{"$":"STUConfigVarDynamic","m_identifier":1},
        "m_bone":bone,
        "m_BD631144":{"$":"STUConfigVarVec3","m_value":{"X":1,"Y":2,"Z":3}}
    })
}

struct Pose(Value);

impl Context for Pose {
    fn variable(&mut self, _: Variable) -> Value {
        self.0.clone()
    }
    fn authored_position(&mut self, entity: u32) -> Option<[f64; 3]> {
        (entity == TARGET).then_some([0.0; 3])
    }
    fn authored_forward(&mut self, entity: u32) -> Option<[f64; 3]> {
        (entity == TARGET).then_some([1.0, 0.0, 0.0])
    }
}

#[test]
fn static_points_missing_observations_and_a_live_body_at_the_origin_are_distinct() {
    let config = query(Json::Null);
    assert_eq!(
        evaluate(&config, &mut Pose(Value::Entity(0))),
        Ok(Value::Vector(LOCAL))
    );
    assert_eq!(
        evaluate(&config, &mut Pose(Value::Null)),
        Err(EvalError::UnavailableEntityPosition(0))
    );
    for omitted in [Json::Null, json!({"$":"ClientOnly", "value":null})] {
        let mut missing = config.clone();
        missing["m_entity"] = omitted;
        assert_eq!(
            evaluate(&missing, &mut EmptyContext),
            Err(EvalError::UnavailableEntityPosition(0))
        );
    }
    assert_eq!(
        evaluate(&config, &mut Pose(Value::Entity(42))),
        Err(EvalError::UnavailableEntityPosition(42))
    );
    assert_eq!(
        evaluate(&config, &mut Pose(Value::Entity(TARGET))),
        Ok(Value::Vector([3.0, 2.0, -1.0]))
    );
    let missing_bone = query(json!({"$":"STUConfigVarBone","m_bone":"0x08800000000000AB"}));
    assert_eq!(
        evaluate(&missing_bone, &mut Pose(Value::Entity(TARGET))),
        Err(EvalError::UnavailableBodyBone {
            entity: TARGET,
            bone: BONE
        })
    );
    assert_eq!(
        evaluate(&missing_bone, &mut Pose(Value::Entity(0))),
        Ok(Value::Vector(LOCAL))
    );
}

#[test]
fn a_bone_query_passes_the_full_guid_and_local_point_to_the_host() {
    struct Model;
    impl Context for Model {
        fn variable(&mut self, _: Variable) -> Value {
            Value::Entity(TARGET)
        }
        fn body_bone_point(&mut self, entity: u32, bone: u64, local: [f64; 3]) -> Option<[f64; 3]> {
            assert_eq!((entity, bone, local), (TARGET, BONE, LOCAL));
            Some([19.0, 21.0, -4.0])
        }
    }
    let config = query(json!({"$":"STUConfigVarBone","m_bone":"0x08800000000000AB"}));
    assert_eq!(
        evaluate(&config, &mut Model),
        Ok(Value::Vector([19.0, 21.0, -4.0]))
    );
}
